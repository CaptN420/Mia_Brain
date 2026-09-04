import socket
socket.create_connection(("8.8.8.8", 53), timeout=2)
print("Connected with --net")
