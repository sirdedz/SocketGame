import sys
import subprocess
import socket
import select
import tiles
import threading
import queue
import random
import traceback
import time
from enum import IntEnum


MAXIMUM_TIME_BETWEEN_RECEIVED_MESSAGES = 10
TURN_THINKING_TIME = 0.2
STATE_MISMATCH_TIME = 0.4


if len(sys.argv) < 2:
  print('usage:\n{} [commands to run server]'.format(sys.argv[0]))
  exit(1)

print('running server with:\n{}'.format(sys.argv[1:]))
# pargs = [sys.executable, 'D:\\Networks\\2021\\AssignmentTest\\server-test.py']
pargs = sys.argv[1:]


class EvServerTerminated:
  def __str__(self):
    return "server process terminated"


class EvPrint:
  def __init__(self, message: str):
    self.message = message

  def __str__(self):
    return self.message


class EvTurn:
  def __str__(self):
    return 'turn'


class EvEliminated:
  def __str__(self):
    return 'eliminated'


class EvWon:
  def __str__(self):
    return 'won'


class EvReset:
  def __str__(self):
    return 'state reset'


class EvUpdated:
  def __str__(self):
    return 'state updated'


class EvConnectionClosed:
  def __str__(self):
    return 'connection closed'


class EvTooQuiet:
  def __str__(self):
    return "didn't hear from the server in a while"


class EvMismatchTimeout:
  def __str__(self):
    return "state mismatch timeout"


class EvClientMessage:
  def __init__(self, msg):
    self.msg = msg

  def __str__(self):
    return "client message {}".format(self.msg)


def get_player_start_tile(board: tiles.Board, idnum: int):
  for x in range(board.width):
    for y in range(board.height):
      tileid, _, playerid = board.get_tile(x, y)
      if tileid != None and playerid == idnum:
        return x, y
  return None


def pick_random_start_position(board: tiles.Board, x: int, y: int):
  available = []

  if x == 0:
    available.extend([7, 6])
  if y == 0:
    available.extend([5, 4])
  if x == board.width - 1:
    available.extend([3, 2])
  if y == board.height - 1:
    available.extend([1, 0])

  return random.choice(available)


def square_is_empty(board: tiles.Board, x: int, y: int):
  index = board.tile_index(x, y)
  return board.tileids[index] == None


def boards_equal(a: tiles.Board, b: tiles.Board):
  for x in range(a.width):
    for y in range(a.height):
      idx = a.tile_index(x, y)
      if a.tileids[idx] != b.tileids[idx]:
        return False, 'tileid mismatch at {}, {} -- {} vs {}'.format(x, y, a.tileids[idx], b.tileids[idx])
      if a.tilerotations[idx] != b.tilerotations[idx]:
        return False, 'tilerotation mismatch at {}, {} -- {} vs {}'.format(x, y, a.tilerotations[idx], b.tilerotations[idx])
      if a.tileplaceids[idx] != b.tileplaceids[idx]:
        return False, 'tileplaceid mismatch at {}, {} -- {} vs {}'.format(x, y, a.tileplaceids[idx], b.tileplaceids[idx])
  if a.playerpositions != b.playerpositions:
    return False, 'playerpositions mismatch -- {} vs {}'.format(a.playerpositions, b.playerpositions)
  return True, None


class Client:
  def __init__(self, tester, events: queue.Queue, server_address, localid):
    self.tester = tester
    self.events = events

    self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.sock.connect(server_address)
    self.sock.setblocking(True)

    self.localid = localid

    # reader thread only
    self.message_timer = None

    # for shared info, below
    self.infolock = threading.Lock()

    # connection length
    self.idnum = None
    self.playernames = {}

    # game length
    self.hand = [None] * tiles.HAND_SIZE
    self.board = tiles.Board()
    self.lasttilelocation = None
    self.location = None
    self.playernums = {}
    self.playerlist = []
    self.eliminatedlist = []
    self.currentplayerid = None
    self.expected_messages = []

    print(' making thread')
    self.reading_thread = threading.Thread(target=self.reader, daemon=True)
    print(' made thread')

  def putevent(self, ev):
    self.events.put((self.localid, ev))

  def print(self, message):
    self.putevent(EvPrint(message))

  def check_basic_state(self, num_expected_players):
    with self.infolock:
      if self.idnum == None:
        return False
      if len(self.playernames) != num_expected_players:
        return False
    return True

  def shared_state_equal(self, other):
    with self.infolock:
      with other.infolock:
        if len(self.playernames) != len(other.playernames):
          return False, 'playernames mismatch'
        if self.playernums != other.playernums:
          return False, 'playernums mismatch'
        if len(self.playerlist) != len(other.playerlist):
          return False, 'playerlist mismatch'
        if self.eliminatedlist != other.eliminatedlist:
          return False, 'eliminatedlist mismatch'
        if self.currentplayerid != other.currentplayerid:
          return False, 'currentplayerid mismatch'
        return boards_equal(self.board, other.board)
    return True, None

  def reset_game_state(self):
    with self.infolock:
      for i in range(len(self.hand)):
        self.hand[i] = None
      self.board.reset()
      self.lasttilelocation = None
      self.location = None
      self.playernums = {}
      self.playerlist.clear()
      self.eliminatedlist.clear()
      self.currentplayerid = None

  def message_timeout(self):
    # print('{} message timer timed out'.format(self.localid))
    self.putevent(EvTooQuiet())
    self.message_timer = None

  def reset_message_timer(self):
    # print('{} resetting message timer'.format(self.localid))
    if self.message_timer != None:
      self.message_timer.cancel()
    self.message_timer = threading.Timer(MAXIMUM_TIME_BETWEEN_RECEIVED_MESSAGES, self.message_timeout)
    self.message_timer.start()

  def reader(self):
    buffer = bytearray()

    infolock = self.infolock

    # self.print('client reader starting')
    self.reset_message_timer()

    while True:
      try:
        chunk = self.sock.recv(4096)
        if chunk:
          buffer.extend(chunk)

          while True:
            msg, consumed = tiles.read_message_from_bytearray(buffer)
            if consumed:
              buffer = buffer[consumed:]

              self.reset_message_timer()

              if isinstance(msg, tiles.MessageWelcome):
                with infolock:
                  self.idnum = msg.idnum
                  self.playernames[msg.idnum] = 'Me!'
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessagePlayerJoined):
                with infolock:
                  self.playernames[msg.idnum] = msg.name
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessagePlayerLeft):
                with infolock:
                  if msg.idnum in app.playernames:
                    del app.playernames[msg.idnum]
                  else:
                    raise RuntimeError("didn't know they were a player")
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessageCountdown):
                pass
              elif isinstance(msg, tiles.MessageGameStart):
                self.print('resetting game state')
                self.reset_game_state()
                self.putevent(EvReset())
                self.putevent(EvUpdated())
                self.print('reset game state')
              elif isinstance(msg, tiles.MessageAddTileToHand):
                tileid = msg.tileid
                if tileid < 0 or tileid > len(tiles.ALL_TILES):
                  raise RuntimeError('unknown tile index {}'.format(tileid))
                with infolock:
                  added = False
                  for i in range(len(self.hand)):
                    if self.hand[i] == None:
                      self.hand[i] = tileid
                      added = True
                      break
                  if not added:
                    raise RuntimeError('adding tile to hand, but hand is full')
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessagePlayerTurn):
                with infolock:
                  if msg.idnum not in self.playernames:
                    raise RuntimeError('unknown playerid {}'.format(msg.idnum))
                  playername = self.playernames[msg.idnum]
                  if not msg.idnum in self.playernums:
                    playernum = len(self.playernums)
                    self.playernums[msg.idnum] = playernum
                    self.playerlist.append(playername)
                  self.currentplayerid = msg.idnum
                  if msg.idnum == self.idnum:
                    self.putevent(EvTurn())
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessagePlaceTile):
                with infolock:
                  if msg.idnum not in self.playernames:
                    raise RuntimeError('unknown playerid {}'.format(msg.idnum))
                  if msg.x < 0 or msg.x >= tiles.BOARD_WIDTH:
                    raise RuntimeError('invalid x {}'.format(msg.x))
                  if msg.y < 0 or msg.y >= tiles.BOARD_HEIGHT:
                    raise RuntimeError('invalid y {}'.format(msg.y))
                  idx = self.board.tile_index(msg.x, msg.y)
                  if self.board.tileids[idx] != None:
                    raise RuntimeError('placing tile on existing tile!')
                  self.board.tileids[idx] = msg.tileid
                  self.board.tilerotations[idx] = msg.rotation
                  self.board.tileplaceids[idx] = msg.idnum
                  if msg.idnum == self.idnum:
                    try:
                      handidx = self.hand.index(msg.tileid)
                    except ValueError:
                      raise RuntimeError('i placed a tile that i do not hold')
                    self.hand[handidx] = None
                    self.lasttilelocation = (msg.x, msg.y)
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessageMoveToken):
                with infolock:
                  if msg.idnum not in self.playernames:
                    raise RuntimeError('unknown playerid {}'.format(msg.idnum))
                  if msg.idnum == self.idnum:
                    self.location = (msg.x, msg.y, msg.position)
                  self.board.update_player_position(msg.idnum, msg.x, msg.y, msg.position)
                self.putevent(EvUpdated())
              elif isinstance(msg, tiles.MessagePlayerEliminated):
                with infolock:
                  if msg.idnum not in self.playernames:
                    raise RuntimeError('unknown playerid {}'.format(msg.idnum))
                  playername = self.playernames[msg.idnum]
                  if playername not in self.playerlist:
                    raise RuntimeError('player eliminated, but not in player list')
                  self.playerlist.remove(playername)
                  if msg.idnum in self.eliminatedlist:
                    raise RuntimeError('player eliminated, but already in eliminated list!')
                  self.eliminatedlist.append(msg.idnum)
                  if msg.idnum == self.idnum:
                    self.putevent(EvEliminated())
                  elif len(self.playerlist) == 1 and self.idnum not in self.eliminatedlist and self.idnum in self.playernums:
                    self.putevent(EvWon())
                self.putevent(EvUpdated())
              else:
                raise RuntimeError('received unknown message')
            else:
              break
        else:
          print(self.idnum, 'chunk empty')
          break
      except Exception as e:
        self.putevent(e)
        break
    self.putevent(EvConnectionClosed())

  def take_turn(self):
    with self.infolock:
      if not self.board.have_player_position(self.idnum):
        tilepos = get_player_start_tile(self.board, self.idnum)
        if tilepos != None:
          x, y = tilepos
          position = pick_random_start_position(self.board, x, y)
          msg = tiles.MessageMoveToken(self.idnum, x, y, position)
          # print('client {} put token at {},{}:{}'.format(self.localid, x, y, position))
          self.putevent(EvClientMessage(msg))
          self.sock.sendall(msg.pack())
          return
        available = []
        for x in range(self.board.width):
          available.append((x, 0))
          available.append((x, self.board.height - 1))
        for y in range(1, self.board.height - 1):
          available.append((0, y))
          available.append((self.board.width - 1, y))
        available = [(x, y) for (x, y) in available if square_is_empty(self.board, x, y)]
        if not available:
          raise RuntimeError('border is full but player has not placed starting tile yet')
        x, y = random.choice(available)
      else:
        x, y, _ = self.board.get_player_position(self.idnum)
      tileid = random.choice(self.hand)
      rotation = random.randrange(0, 4)
    msg = tiles.MessagePlaceTile(self.idnum, tileid, rotation, x, y)
    # print('client {} place tile {} at {},{}:{}'.format(self.localid, tileid, x, y, rotation))
    self.putevent(EvClientMessage(msg))
    self.sock.sendall(msg.pack())

  def close_and_join(self):
    try:
      self.sock.close()
    except Exception as e:
      print('close error {}'.format(e))
    # self.reading_thread.join()


class ProcessEventResult(IntEnum):
  NOTHING_EXCITING = 1
  PLAYER_SET_TOKEN = 2


class Tester:
  def __init__(self, pargs):
    self.pargs = pargs

    self.events = queue.Queue()
    self.server_address = ('localhost', 30020)

    self.games_finished = 0

    self.boardlock = threading.Lock()
    self.reset_local_board_state()

    self.next_client_id = -1
    self.clients = []
    self.clientmap = {}

    self.take_turn_timer = None
    self.state_mismatch_timer = None

  def __enter__(self):
    self.proc = subprocess.Popen(self.pargs)
    self.proc.__enter__()

    self.proc_wait_thread = threading.Thread(target=self.wait_for_subprocess_termination, daemon=True)
    self.proc_wait_thread.start()

    return self

  def __exit__(self, type, value, traceback):
    print('going to close clients')

    self.close_all_clients()

    print('closed clients')

    print('terminating server')

    self.proc.terminate()
    try:
      self.proc.wait(2)
    except subprocess.TimeoutExpired:
      print('terminate timed out, killing server')
      self.proc.kill()

    self.proc.wait(2)

    print('server (hopefully) down')

    self.proc.__exit__(type, value, traceback)

  def wait_for_subprocess_termination(self):
    self.proc.wait()
    self.events.put((None, EvServerTerminated()))

  def add_client(self):
    self.next_client_id += 1
    client = Client(self, self.events, self.server_address, self.next_client_id)
    self.clients.append(client)
    self.clientmap[self.next_client_id] = client
    client.reading_thread.start()

  def take_turn_timeout(self, clientid):
    # print('take turn {}'.format(clientid))
    self.take_turn_timer = None
    try:
      self.clientmap[clientid].take_turn()
    except Exception as e:
      print('exception taking turn for client {}: {}'.format(clientid, e))
      exc_type, exc_value, exc_traceback = sys.exc_info()
      traceback.print_exception(exc_type, exc_value, exc_traceback, limit=2, file=sys.stdout)

  def cancel_take_turn_timer(self):
    if self.take_turn_timer:
      self.take_turn_timer.cancel()
      self.take_turn_timer = None

  def set_take_turn_timer(self, clientid, timeout=TURN_THINKING_TIME):
    if self.take_turn_timer:
      self.take_turn_timer.cancel()
    self.take_turn_timer = threading.Timer(timeout, self.take_turn_timeout, args=[clientid])
    self.take_turn_timer.start()

  def set_current_turn(self, clientid, idnum):
    if idnum not in self.all_idnums:
      self.all_idnums.append(idnum)
      self.live_idnums.append(idnum)

  def complain_state_mismatch(self):
    self.state_mismatch_timer = None
    self.events.put((None, EvMismatchTimeout()))

  def cancel_state_mismatch_timer(self):
    if self.state_mismatch_timer:
      self.state_mismatch_timer.cancel()
      self.state_mismatch_timer = None

  def set_state_mismatch_timer(self, timeout=STATE_MISMATCH_TIME):
    if self.state_mismatch_timer:
      self.state_mismatch_timer.cancel()
    self.state_mismatch_timer = threading.Timer(timeout, self.complain_state_mismatch)
    self.state_mismatch_timer.start()

  def add_expected_message(self, msg):
    pass

  def all_client_states_equal(self):
    if self.clients:
      a = self.clients[0]
      for i in range(1, len(self.clients)):
        clienteq, reason = a.shared_state_equal(self.clients[i])
        if not clienteq:
          return clienteq, "clients {} and {}: {}".format(a.localid, self.clients[i].localid, reason)
    return True, None

  def all_clients_have_expected_board(self):
    with self.boardlock:
      for client in self.clients:
        with client.infolock:
          boardeq, reason = boards_equal(client.board, self.board)
          if not boardeq:
            return boardeq, "client {}: {}".format(client.localid, reason)
    return True, None

  def process_next_turn_messages(self):
    positionupdates, eliminated = self.board.do_player_movement(self.live_idnums)

    for positionupdate in positionupdates:
      self.add_expected_message(positionupdate)

    for idnum in eliminated:
      self.live_idnums.remove(idnum)
      self.eliminated_idnums.append(idnum)
      self.add_expected_message(tiles.MessagePlayerEliminated(idnum))

    # and message player turn for the next player idnum

  def process_client_message(self, msg):
    with self.boardlock:
      board = self.board

      if msg.idnum not in self.all_idnums:
        self.all_idnums.append(msg.idnum)
        self.live_idnums.append(msg.idnum)

      if isinstance(msg, tiles.MessagePlaceTile):
        # if state.have_tile_in_hand(msg.tileid) and msg.idnum == state.idnum:
        if board.set_tile(msg.x, msg.y, msg.tileid, msg.rotation, msg.idnum):
          self.add_expected_message(msg)
          # new tile message
          self.process_next_turn_messages()
          self.turn_client_id = None
      elif isinstance(msg, tiles.MessageMoveToken):
        if not board.have_player_position(msg.idnum): # and msg.idnum == state.idnum:
          if board.set_player_start_position(msg.idnum, msg.x, msg.y, msg.position):
            self.process_next_turn_messages()
            self.turn_client_id = None

  def check_all_states_match(self):
    for client in self.clients:
      if not client.check_basic_state(len(self.clients)):
        raise Exception('{}: basic state not ready'.format(client.localid))

    boardsequal, reason = self.all_clients_have_expected_board()
    if not boardsequal:
      raise Exception('not all client boards equal: {}'.format(reason))

    stateequal, reason = self.all_client_states_equal()
    if not stateequal:
      raise Exception('shared state not equal: {}'.format(reason))

  def process_next_event(self):
    result = ProcessEventResult.NOTHING_EXCITING

    clientid, msg = self.events.get()

    # print('{}: {}'.format(clientid, msg))

    if isinstance(msg, EvServerTerminated):
      print('server terminated')
      raise Exception('server terminated unexpectedly!')
    elif isinstance(msg, EvTooQuiet):
      raise Exception("{} hasn't heard from the server in a while".format(clientid))
    elif isinstance(msg, EvMismatchTimeout):
      self.check_all_states_match()
      # if we get to here, ignore this timeout, the state has become equal
    elif isinstance(msg, EvClientMessage):
      self.process_client_message(msg.msg)
      if isinstance(msg.msg, tiles.MessageMoveToken):
        result = ProcessEventResult.PLAYER_SET_TOKEN
    elif isinstance(msg, EvTurn):
      with self.boardlock:
        self.turn_client_id = clientid
        self.set_current_turn(clientid, self.clientmap[clientid].idnum)
    elif isinstance(msg, EvEliminated):
      pass
    elif isinstance(msg, EvWon):
      self.turn_client_id = None
      self.games_finished += 1
    elif isinstance(msg, EvReset):
      print('resetting local board state')
      self.reset_local_board_state()
    elif isinstance(msg, EvUpdated):
      match = False

      try:
        self.check_all_states_match()
        match = True
      except Exception:
        pass

      if match:
        # print('states match')
        self.cancel_state_mismatch_timer()
        with self.boardlock:
          if self.turn_client_id != None:
            # print('setting turn timer for client {}'.format(self.turn_client_id))
            self.set_take_turn_timer(self.turn_client_id)
      else:
        # print('states mismatch, cancelling turn timer')
        self.set_state_mismatch_timer()
        self.cancel_take_turn_timer()

    return result

  def reset_local_board_state(self):
    with self.boardlock:
      self.board = tiles.Board()
      self.all_idnums = []
      self.live_idnums = []
      self.eliminated_idnums = []
      self.turn_client_id = None

  def close_all_clients(self):
    for client in self.clients:
      client.close_and_join()


def run_a_test(num_initial=2, num_during=0, num_games=1):
  games_finished = 0

  with Tester(pargs) as tester:
    time.sleep(1)

    for _ in range(num_initial):
      tester.add_client()

    try:
      added_during = False

      while True:
        result = tester.process_next_event()

        if tester.games_finished >= num_games:
          break

        if result == ProcessEventResult.PLAYER_SET_TOKEN and not added_during:
          added_during = True
          for _ in range(num_during):
            tester.add_client()
    except Exception as e:
      exc_type, exc_value, exc_traceback = sys.exc_info()
      traceback.print_exception(exc_type, exc_value, exc_traceback, limit=2, file=sys.stdout)
      return 'EXCEPTION {}'.format(e)

  return 'SUCCESS'

test_results = []

test_results.append('TWO PLAYERS: {}'.format(run_a_test()))
test_results.append('TWO PLAYERS x TWO GAMES: {}'.format(run_a_test(num_games=2)))
test_results.append('FOUR PLAYERS: {}'.format(run_a_test(num_initial=4)))
test_results.append('FOUR PLAYERS x TWO GAMES: {}'.format(run_a_test(num_initial=4, num_games=2)))
test_results.append('TWO PLAYERS + TWO NEW, TWO GAMES: {}'.format(run_a_test(num_during=2, num_games=2)))

for result in test_results:
  print(result)
