#!/usr/bin/env python3
"""Launcher script for the backoffice interface."""

import os
import sys
import tkinter as tk

# Ensure the main module is importable when this script is launched from
# another directory by inserting the script's folder onto ``sys.path``.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cash_register_gui_enhanced import CashRegisterApp, configure_styles

if __name__ == "__main__":
    root = tk.Tk()
    configure_styles(root)
    app = CashRegisterApp(root, mode="backoffice")
    root.mainloop()
