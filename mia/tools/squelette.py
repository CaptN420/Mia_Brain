import tkinter as tk
from tkinter import ttk


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mon App")
        self.geometry("800x500")

        self.label = ttk.Label(self, text="Salut bro")
        self.label.pack(padx=20, pady=20)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
