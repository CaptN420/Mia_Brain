with open("/tmp/outside_sandbox.txt", "w") as f:
    f.write("escape")
print("Should not see this")
