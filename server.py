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
import random

# countdown time in seconds before a game starts
countdown = 0



# global variables used for game
turn_index = 0
turn_order = []

board = tiles.Board()
placements = []
current_tokens = []
players_eliminated = []

players = {}
players_remaining = []

playerno = 0


# send a message to all clients connected to the server
def send_to_all(msg):
    for key in players:
        key.send(msg)

# send a message to all clients except specified client
def send_to_others(msg, current_con):
    for key in players:
        if key is not current_con:
            key.send(msg)

# clear the variables associated with a client on disconnection
def disconnect_player(connection, id):
    if len(players) == 1:
        players_remaining.clear()
        turn_order.clear()
        players.clear()
    else:
        if id in players_remaining:
            players_remaining.remove(id)

        if id in turn_order:
            turn_order.remove(id)

        del players[connection]

# check to see if the game should finish, and if a new game should start
def check_game_over(con):
    global in_progress

    if len(players_remaining) == 1 and len(players) >= 2:
        #Game has finished, new game needed
        print('Game Over, starting new game...')
        in_progress = True
        turn_order.clear()
        placements.clear()
        start_game()
        return True

    elif len(players_remaining) == 1:
        print('Game over')
        turn_order.clear()
        placements.clear()
        in_progress = False
        return True

    return False




def client_handler(lock, connection, address):
  host, port = address
  name = '{}:{}'.format(host, port)

  idnum = players[connection].id

  buffer = bytearray()

  while True:
    chunk = connection.recv(4096)
    if not chunk:
      # handle client disconnection
      print('client {} disconnected'.format(address))

      # increment turns if it was that players turn
      if len(players) > 1:
          if idnum in turn_order:
              with lock:
                  turn_order.remove(idnum)
                  send_to_others(tiles.MessagePlayerTurn(turn_order[turn_index]).pack(), connection)


      # run the disconnect client function
      with lock:
          disconnect_player(connection, idnum)

          # let other clients know the client has been eliminated, add to players eliminated
          if idnum not in players_eliminated:
              send_to_others(tiles.MessagePlayerEliminated(idnum).pack(), connection)
              players_eliminated.append(idnum)

      send_to_others(tiles.MessagePlayerLeft(idnum).pack(), connection)

      # check if client disconnection should cause came to finish
      if check_game_over(connection):
          pass

      return

    buffer.extend(chunk)

    while True:
      # handle messages from client
      msg, consumed = tiles.read_message_from_bytearray(buffer)
      if not consumed:
        break

      buffer = buffer[consumed:]

      print('received message {}, from id: '.format(msg), idnum)

      # sent by the player to put a tile onto the board (in all turns except
      # their second)
      if isinstance(msg, tiles.MessagePlaceTile) and idnum == turn_order[turn_index]:
        if board.set_tile(msg.x, msg.y, msg.tileid, msg.rotation, msg.idnum):
          # notify client that placement was successful
          send_to_all(msg.pack())

          # add tile place to placement history
          tile_msg = [msg.idnum, msg.tileid, msg.rotation, msg.x, msg.y]
          placements.append(tile_msg)

          # check for token movement
          positionupdates, eliminated = board.do_player_movement(players_remaining)

          for msg in positionupdates:
            send_to_all(msg.pack())

            # record up to date position of token
            token_msg = [msg.idnum, msg.x, msg.y, msg.position]
            current_tokens.append(token_msg)

          # check for resulting eliminated players
          for id in players_remaining:
              if id in eliminated and id not in players_eliminated:
                # let all clients know this client has been eliminated
                send_to_all(tiles.MessagePlayerEliminated(id).pack())

                # remove eliminated client from players remaining, add to players eliminated
                players_remaining.remove(id)
                players_eliminated.append(id)

                if id in turn_order:
                    with lock:
                        turn_order.remove(id)

                send_to_others(tiles.MessagePlayerTurn(turn_order[turn_index]).pack(), connection)

                # check to see if client eliminated should cause game to finish
                if check_game_over(connection):
                    break

          # pickup a new tile and remove placed tile from hand
          if tile_msg[1] in players[connection].hand:
              players[connection].hand.remove(tile_msg[1])
          tileid = tiles.get_random_tileid()
          players[connection].hand.append(tileid)
          connection.send(tiles.MessageAddTileToHand(tileid).pack())

          # start next turn, increment the turn index and send next turn to all clients
          if idnum in turn_order:
              with lock:
                  turn_order.remove(idnum)
                  turn_order.append(idnum)
          send_to_all(tiles.MessagePlayerTurn(turn_order[turn_index]).pack())

      # sent by the player in the second turn, to choose their token's
      # starting path
      elif isinstance(msg, tiles.MessageMoveToken) and idnum == turn_order[turn_index]:
        if not board.have_player_position(msg.idnum):
          if board.set_player_start_position(msg.idnum, msg.x, msg.y, msg.position):
            # check for token movement
            positionupdates, eliminated = board.do_player_movement(players_remaining)

            for msg in positionupdates:
              send_to_all(msg.pack())

              # record up to date position of token
              token_msg = [msg.idnum, msg.x, msg.y, msg.position]
              current_tokens.append(token_msg)


            if idnum in eliminated and idnum not in players_eliminated:
              # let clients know player has been eliminated
              send_to_all(tiles.MessagePlayerEliminated(idnum).pack())

              # remove eliminated client from players remaining, add to players eliminated
              players_remaining.remove(idnum)
              players_eliminated.append(idnum)

              if idnum in turn_order:
                  with lock:
                      turn_order.remove(idnum)

              send_to_others(tiles.MessagePlayerTurn(turn_order[turn_index]).pack(), connection)

              # check if this player being eliminated should cause the game to finish
              if check_game_over(connection):
                  break

            # start next turn, increment the turn index and send next turn to all clients
            if idnum in turn_order:
                with lock:
                    #turn_index = (turn_index + 1) % len(turn_order)
                    turn_order.remove(idnum)
                    turn_order.append(idnum)
            send_to_all(tiles.MessagePlayerTurn(turn_order[turn_index]).pack())



# create a TCP/IP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# listen on all network interfaces
server_address = ('', 30020)
sock.bind(server_address)

sock.setblocking(True)

print('listening on {}'.format(sock.getsockname()))

sock.listen(5)


# class to consolidate a clients id and address
class Player():
    def __init__(self, address, id, hand):
        self.address = address
        self.id = id
        self.hand = hand




# handle starting a new game
def start_game():
    in_progress = True

    global board
    board.reset()

    global placements
    global current_tokens
    global players_remaining
    global players_eliminated

    placements.clear()
    current_tokens.clear()


    # set the turn order and index, stop all threads for spectating for the players joining
    # the game
    total_players = 0
    available_ids = []

    available_ids.clear()
    players_remaining.clear()
    players_eliminated.clear()

    for key in players:
        total_players += 1
        available_ids.append(players[key].id)

        #clear all players' previous hands
        players[key].hand.clear()

    # choose players for game from player pool
    for a in range(min(4, total_players)):
        # get random index
        index = random.randrange(len(available_ids))

        # get ID from random index and add to turn order
        id = available_ids.pop(index)
        turn_order.append(id)
        players_remaining.append(id)


    # prevent race conditions
    lock = threading.RLock()

    # reset turn index
    global turn_index
    turn_index = 0


    # countdown until start
    for x in range(0, countdown):
        print('starting game in: ', countdown - x)
        threading.Event().wait(1)

    print('starting game...')
    print(turn_order)

    ##------------------------------------------------------------##
    # Client communication:

    # let the clients know that the game is starting
    for key in players:
        key.send(tiles.MessageWelcome(players[key].id).pack())

    send_to_all(tiles.MessageGameStart().pack())

    # let clients know of turn order
    for id in turn_order:
        send_to_all(tiles.MessagePlayerTurn(id).pack())

    # let clients know of actual current turn
    send_to_all(tiles.MessagePlayerTurn(turn_order[turn_index]).pack())

    # send hand to each client
    for key in players:
        if players[key].id in players_remaining:
            # client chooses tiles randomly
            for _ in range(tiles.HAND_SIZE):
              tileid = tiles.get_random_tileid()
              players[key].hand.append(tileid)
              key.send(tiles.MessageAddTileToHand(tileid).pack())

    ##------------------------------------------------------------##




# constantly listen for any new connections
in_progress = False
while True:
  # handle each new connection independently
  connection, client_address = sock.accept()
  players[connection] = Player(client_address, playerno, [])

  lock = threading.RLock()


  # start thread for the client to spectate
  threading.Thread(target=client_handler, args=(lock, connection, client_address), daemon=True).start()

  playerno += 1

  print('received connection from {}'.format(client_address))

  # let the client know of the other players on the server
  for key in players:
      if key is not connection:
          other_host, other_port = players[key].address
          other_name = '{}:{}'.format(other_host, other_port)
          connection.send(tiles.MessagePlayerJoined(other_name, players[key].id).pack())

  # let the existing clients know of this client joining the server
  host, port = client_address
  name = '{}:{}'.format(host, port)
  send_to_others(tiles.MessagePlayerJoined(name, players[connection].id).pack(), connection)


  # let the client know of the current state of the game if a game is in progress
  if in_progress:
      for t in range(len(placements)):
          place = tiles.MessagePlaceTile(placements[t][0], placements[t][1], placements[t][2], placements[t][3], placements[t][4])
          connection.send(place.pack())

      for a in range(len(current_tokens)):
          tok = tiles.MessageMoveToken(current_tokens[a][0], current_tokens[a][1],current_tokens[a][2],current_tokens[a][3])
          connection.send(tok.pack())

      for id in players_eliminated:
          connection.send(tiles.MessagePlayerEliminated(id).pack())

      for id in turn_order:
          connection.send(tiles.MessagePlayerTurn(id).pack())

      connection.send(tiles.MessagePlayerTurn(turn_order[turn_index]).pack())


  # start the game if enough players are spectating and a game is not in progress
  if (len(players) >= 2) and not in_progress:
      in_progress = True
      turn_order.clear()
      start_game()
