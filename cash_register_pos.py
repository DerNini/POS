import os
import sys
import tkinter as tk

# Make sure the enhanced GUI module can be resolved when this script is
# executed from outside the repository root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cash_register_gui_enhanced import CashRegisterApp, configure_styles

if __name__ == "__main__":
    root = tk.Tk()
    configure_styles(root)
    app = CashRegisterApp(root, mode="pos")
    root.mainloop()
