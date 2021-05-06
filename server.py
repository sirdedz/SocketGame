# CITS3002 2021 Assignment
#
# This file implements a basic server that allows a single client to play a
# single game with no other participants, and very little error checking.
#
# Any other clients that connect during this time will need to wait for the
# first client's game to complete.
#
# Your task will be to write a new server that adds all connected clients into
# a pool of players. When enough players are available (two or more), the server
# will create a game with a random sample of those players (no more than
# tiles.PLAYER_LIMIT players will be in any one game). Players will take turns
# in an order determined by the server, continuing until the game is finished
# (there are less than two players remaining). When the game is finished, if
# there are enough players available the server will start a new game with a
# new selection of clients.

import socket
import sys
import tiles
import threading


countdown = 5

def send_to_all(connections, msg):
     for connection in connections:
         connection.send(msg)

def send_to_others(connections, msg, current_con):
    for connection in connections:
        if connection is not current_con:
            connection.send(msg)

def disconnect_player(connection):
    del players[connection]

turn_index = 0
turn_order = []
board = tiles.Board()


def client_handler(lock, connection, address, players):
  host, port = address
  name = '{}:{}'.format(host, port)

  idnum = players[connection]
  print(idnum)

  live_idnums = []
  connections = []

  for key in players:
      connections.append(key)
      live_idnums.append(players[key])

  next_id = live_idnums[(idnum + 1) % len(live_idnums)]

  global turn_index

  connection.send(tiles.MessageWelcome(idnum).pack())
  connection.send(tiles.MessageGameStart().pack())
  send_to_others(connections, tiles.MessagePlayerJoined(name, idnum).pack(), connection)


  for _ in range(tiles.HAND_SIZE):
    tileid = tiles.get_random_tileid()
    connection.send(tiles.MessageAddTileToHand(tileid).pack())

  connection.send(tiles.MessagePlayerTurn(idnum).pack())

  global board

  with lock:
      board.reset()

  buffer = bytearray()

  while True:
    chunk = connection.recv(4096)
    if not chunk:
      print('client {} disconnected'.format(address))
      connection.close()
      disconnect_player(connection)
      send_to_others(connections, tiles.MessagePlayerEliminated(idnum).pack(), connection)
      return

    buffer.extend(chunk)

    while True:
      msg, consumed = tiles.read_message_from_bytearray(buffer)
      if not consumed:
        break

      buffer = buffer[consumed:]

      print('received message {}, from id: '.format(msg), idnum)

      # sent by the player to put a tile onto the board (in all turns except
      # their second)
      if isinstance(msg, tiles.MessagePlaceTile) and idnum == list(players.values())[turn_index]:
        if board.set_tile(msg.x, msg.y, msg.tileid, msg.rotation, msg.idnum):
          # notify client that placement was successful
          send_to_all(connections, msg.pack())

          # check for token movement
          positionupdates, eliminated = board.do_player_movement(live_idnums)

          for msg in positionupdates:
            send_to_all(connections, msg.pack())

          if idnum in eliminated:
            send_to_all(connections, tiles.MessagePlayerEliminated(idnum).pack())
            return

          # pickup a new tile
          tileid = tiles.get_random_tileid()
          connection.send(tiles.MessageAddTileToHand(tileid).pack())

          # start next turn
          with lock:
              turn_index = (turn_index + 1) % len(players)
          send_to_all(connections, tiles.MessagePlayerTurn(next_id).pack())

      # sent by the player in the second turn, to choose their token's
      # starting path
      elif isinstance(msg, tiles.MessageMoveToken) and idnum == list(players.values())[turn_index]:
        if not board.have_player_position(msg.idnum):
          if board.set_player_start_position(msg.idnum, msg.x, msg.y, msg.position):
            # check for token movement
            positionupdates, eliminated = board.do_player_movement(live_idnums)

            for msg in positionupdates:
              send_to_all(connections, msg.pack())

            if idnum in eliminated:
              send_to_all(connections, tiles.MessagePlayerEliminated(idnum).pack())
              return

            # start next turn
            with lock:
                turn_index = (turn_index + 1) % len(players)
            send_to_all(connections, tiles.MessagePlayerTurn(next_id).pack())


# create a TCP/IP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# listen on all network interfaces
server_address = ('', 30020)
sock.bind(server_address)

sock.setblocking(True)

print('listening on {}'.format(sock.getsockname()))

sock.listen(5)


players = {}
client_addresses = []

playerno = 0

while True:
  # handle each new connection independently
  connection, client_address = sock.accept()
  players[connection] = playerno
  client_addresses.append(client_address)

  playerno += 1

  print('received connection from {}'.format(client_address))

#NOTE: In present state to allow more than 2 players in the game the clients must be joined and waiting for the 2 player game to finish
#      Perhaps use a countdown in future for clients to join
  if (len(players) >= 2):
      for x in range(0, countdown):
          print('starting game in: ', countdown - x)
          threading.Event().wait(1)

      print('starting game...')

      turn_index = 0

      #prevent race conditions
      lock = threading.RLock()

      for i in range(len(players)):
        threading.Thread(target=client_handler, args=(lock, list(players)[i], client_addresses[i], players,), daemon=True).start()

'''
      i = 0
      number_of_players = min(4, len(players))
      selected_players_indexes = []
      players_in_game = {}

      #pick up to 4 players for the game
      for a in range(0, number_of_players):
          selected_index = random.randrage(0, len(players)-1)
          selected_players_indexes.append(selected_index)
          key = list(players)[selected_index]
          players_in_game[key] = players[key]
'''
