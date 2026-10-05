#!/usr/bin/env python3
"""Render icon candidates in tk so we can see which actually have glyphs (no tofu)."""
import tkinter as tk

CANDS = [
    ("2733", "\u2733"), ("2726", "\u2726"), ("2727", "\u2727"), ("2731", "\u2731"),
    ("25C6", "\u25c6"), ("25C7", "\u25c7"), ("25CF", "\u25cf"), ("25CB", "\u25cb"),
    ("25B2", "\u25b2"), ("25A0", "\u25a0"), ("25A1", "\u25a1"), ("25D0", "\u25d0"),
    ("25C9", "\u25c9"), ("2B22", "\u2b22"), ("2B21", "\u2b21"), ("2B24", "\u2b24"),
    ("26A0", "\u26a0"), ("26A1", "\u26a1"), ("2699", "\u2699"), ("21BB", "\u21bb"),
    ("21C4", "\u21c4"), ("2318", "\u2318"), ("25AA", "\u25aa"), ("2724", "\u2724"),
    ("2708", "\u2708"), ("2605", "\u2605"), ("2606", "\u2606"), ("29BF", "\u29bf"),
]

root = tk.Tk()
root.overrideredirect(True)
root.configure(bg="#0b0b11")
for i, (cp, ch) in enumerate(CANDS):
    tk.Label(root, text=f"{cp}", bg="#0b0b11", fg="#8a8a99",
             font=("DejaVu Sans Mono", 9)).grid(row=i // 6, column=(i % 6) * 2, padx=(10, 2), pady=8)
    for col, fam in enumerate(("DejaVu Sans", "DejaVu Sans Mono", "Ubuntu")):
        tk.Label(root, text=ch, bg="#0b0b11", fg="#e6e6f0",
                 font=(fam, 15)).grid(row=i // 6, column=(i % 6) * 2 + 1,
                                      padx=(0, 10), pady=8)
root.update_idletasks()
root.mainloop()
