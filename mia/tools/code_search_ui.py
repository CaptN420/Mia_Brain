import os
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

def open_file():
    filepath = filedialog.askopenfilename(
        filetypes=[("Python Files", "*.py"), ("All Files", "*.*")]
    )
    if filepath:
        file_entry.delete(0, tk.END)
        file_entry.insert(0, filepath)

def search_text(event=None):
    filepath = file_entry.get().strip()
    keyword = search_entry.get().strip()

    result_box.delete("1.0", tk.END)

    if not filepath:
        messagebox.showwarning("Warning", "Select a file first.")
        return

    if not os.path.isfile(filepath):
        messagebox.showerror("Error", f"File not found:\n{filepath}")
        return

    if not keyword:
        messagebox.showwarning("Warning", "Enter a keyword.")
        return

    found = 0

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if keyword.lower() in line.lower():
                    result_box.insert(tk.END, f"{i}: {line}")
                    found += 1

        if found == 0:
            result_box.insert(tk.END, "No match found.\n")

    except Exception as e:
        messagebox.showerror("Error", str(e))

root = tk.Tk()
root.title("Python Code Search Tool")
root.geometry("900x600")

top_frame = tk.Frame(root)
top_frame.pack(fill="x", padx=8, pady=8)

file_entry = tk.Entry(top_frame)
file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

browse_btn = tk.Button(top_frame, text="Browse", command=open_file)
browse_btn.pack(side="left")

search_frame = tk.Frame(root)
search_frame.pack(fill="x", padx=8, pady=(0, 8))

search_entry = tk.Entry(search_frame)
search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
search_entry.bind("<Return>", search_text)

search_btn = tk.Button(search_frame, text="Search", command=search_text)
search_btn.pack(side="left")

result_box = scrolledtext.ScrolledText(root, wrap="word")
result_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

root.mainloop()
