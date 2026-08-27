import sys
# tries to write OUTSIDE the shield -> must be blocked
try:
    with open("C:/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main/_leak_test.txt", "w") as f:
        f.write("leak")
    print("LEAKED - shield failed")
except PermissionError:
    print("BLOCKED - shield works")

# allowed write inside sandbox
with open("inside_ok.txt", "w") as f:
    f.write("contained")
print("inside write OK")
