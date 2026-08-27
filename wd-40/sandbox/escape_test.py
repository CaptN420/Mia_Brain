import os, subprocess
# 1) try to open a file OUTSIDE the shield for writing (should be blocked)
try:
    with open("C:/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main/_escape.txt", "w") as f:
        f.write("x")
    print("ESCAPE-1 LEAKED")
except PermissionError:
    print("ESCAPE-1 BLOCKED (write outside shield)")
# 2) try to spawn a process outside (should be blocked)
try:
    subprocess.Popen(["notepad.exe"])
    print("ESCAPE-2 LEAKED (spawn)")
except PermissionError:
    print("ESCAPE-2 BLOCKED (subprocess spawn)")
except FileNotFoundError:
    print("ESCAPE-2 BLOCKED (subprocess spawn - exe not found, but hook did not stop it)")
# 3) symlink inside shield pointing outside, then write via it
link = "trap_link.txt"
if not os.path.exists(link):
    try:
        os.symlink("C:/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main/_escape2.txt", link)
        with open(link, "w") as f: f.write("x")
        print("ESCAPE-3 LEAKED (symlink)")
    except PermissionError:
        print("ESCAPE-3 BLOCKED (symlink)")
    except OSError:
        print("ESCAPE-3 skipped (symlink create denied)")
