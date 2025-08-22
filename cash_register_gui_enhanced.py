import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple, Callable
import json
import sqlite3

AGE_CATEGORIES = [0, 6, 12, 16, 18]

ROLE_CHOICES = (
    "Admin",
    "Kassierer",
    "Lagerist",
    "Steuerberater",
    "Filialleiter",
    "Techniker",
)

SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "version": "1.0.0",
    "debug": False,
    "store_name": "Kassensystem",
    "currency": "€",
    "auto_save_receipts": True,
    "auto_save_logs": False,
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {**DEFAULT_SETTINGS, **data}
    except FileNotFoundError:
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def configure_styles(root: tk.Tk):
    """Apply a cleaner ttk theme and widget styles."""
    style = ttk.Style(root)
    style.theme_use("clam")
    # Use a uniform, slightly larger default font
    # Wrap the family name in braces so Tk treats "Segoe UI" as a single token
    # otherwise spaces confuse the font parser on some systems
    root.option_add("*Font", "{Segoe UI} 12")
    style.configure("TButton", padding=6)
    style.configure("Treeview", rowheight=24)
    style.configure("Treeview.Heading", font=("Segoe UI", 12, "bold"))
    style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))


def format_receipt_text(receipt: dict, store_name: str, currency: str) -> str:
    lines = [store_name, "==== Kassenzettel ===="]
    for item in receipt["items"]:
        price_gross = item["price"] * (1 + item.get("tax_rate", 0) / 100)
        lines.append(
            f"{item['quantity']} x {item['name']} @ {price_gross:.2f} {currency} = {item['total']:.2f} {currency}"
        )
    lines.append("----------------------")
    lines.append(f"Zwischensumme: {receipt['net']:.2f} {currency}")
    lines.append(f"Steuer: {receipt['tax']:.2f} {currency}")
    lines.append(f"Gesamt: {receipt['total']:.2f} {currency}")
    lines.append(f"Datum : {receipt['timestamp']}")
    if receipt.get("cashier"):
        lines.append(f"Kassierer: {receipt['cashier']}")
    lines.append("======================")
    return "\n".join(lines)


def format_daily_close_text(entry: dict, store_name: str, currency: str) -> str:
    lines = [store_name, f"==== Tagesabschluss {entry.get('number', '')} ===="]
    lines.append(f"Netto: {entry.get('net', 0):.2f} {currency}")
    lines.append(f"Steuer: {entry.get('tax', 0):.2f} {currency}")
    lines.append(f"Gesamt: {entry.get('total', 0):.2f} {currency}")
    lines.append(f"Datum : {entry.get('timestamp', '')}")
    lines.append("======================")
    return "\n".join(lines)


@dataclass
class Product:
    sku: str
    name: str
    price: float
    stock: int
    min_age: Optional[int] = None
    tax_rate: float = 0.0


@dataclass
class Cashier:
    personnel_number: str
    pin: str
    name: str
    role: str


class CashRegister:
    def __init__(self):
        # Separate databases for inventory, cashiers and tax rates
        self.inventory_conn = sqlite3.connect("inventory.db")
        self.inventory_conn.row_factory = sqlite3.Row
        self.inventory_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                name TEXT,
                price REAL,
                stock INTEGER,
                min_age INTEGER,
                tax_rate REAL
            )
            """
        )

        self.cashier_conn = sqlite3.connect("users.db")
        self.cashier_conn.row_factory = sqlite3.Row
        self.cashier_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cashiers (
                personnel_number TEXT PRIMARY KEY,
                pin TEXT,
                name TEXT,
                role TEXT
            )
            """
        )

        # Ensure legacy databases have required columns
        columns = {
            row["name"] for row in self.cashier_conn.execute("PRAGMA table_info(cashiers)")
        }
        if "role" not in columns:
            self.cashier_conn.execute(
                "ALTER TABLE cashiers ADD COLUMN role TEXT DEFAULT 'Kassierer'"
            )
            self.cashier_conn.commit()
        pcols = {
            row["name"] for row in self.inventory_conn.execute("PRAGMA table_info(products)")
        }
        if "tax_rate" not in pcols:
            self.inventory_conn.execute(
                "ALTER TABLE products ADD COLUMN tax_rate REAL DEFAULT 0"
            )
            self.inventory_conn.commit()

        # Steuerverwaltung
        self.tax_conn = sqlite3.connect("taxes.db")
        self.tax_conn.row_factory = sqlite3.Row
        self.tax_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tax_rates (
                rate REAL PRIMARY KEY
            )
            """
        )
        self.tax_rates = [row["rate"] for row in self.tax_conn.execute("SELECT rate FROM tax_rates")]
        if not self.tax_rates:
            self.add_tax_rate(19.0)
            self.add_tax_rate(7.0)

        self.catalog = {}
        for row in self.inventory_conn.execute(
            "SELECT sku,name,price,stock,min_age,tax_rate FROM products"
        ):
            self.catalog[row["sku"]] = Product(
                row["sku"],
                row["name"],
                row["price"],
                row["stock"],
                row["min_age"],
                row["tax_rate"] or 0.0,
            )

        self.cashiers = {}
        for row in self.cashier_conn.execute(
            "SELECT personnel_number,pin,name,role FROM cashiers"
        ):
            self.cashiers[row["personnel_number"]] = Cashier(
                row["personnel_number"], row["pin"], row["name"], row["role"]
            )
        if not self.cashiers:
            # Seed a default administrative account with predictable credentials
            self.add_cashier("admin", "admin", "admin", "Admin")
            # Provide example accounts for other roles
            self.add_cashier("1002", "5678", "Bob", "Kassierer")
            self.add_cashier("1003", "0000", "Charlie", "Lagerist")
            self.add_cashier("1004", "1111", "Doris", "Steuerberater")
            self.add_cashier("1005", "2222", "Eve", "Filialleiter")
            self.add_cashier("1006", "3333", "Theo", "Techniker")

        try:
            with open("receipts.json", "r", encoding="utf-8") as f:
                self.receipts: List[dict] = json.load(f)
        except FileNotFoundError:
            self.receipts = []
        self.inventory_log: List[dict] = []
        try:
            with open("daily_close_journal.json", "r", encoding="utf-8") as f:
                self.daily_close_journal: List[dict] = json.load(f)
        except FileNotFoundError:
            self.daily_close_journal = []

        # Stammdaten für Kassenschubladen
        self.drawer_conn = sqlite3.connect("drawers.db")
        self.drawer_conn.row_factory = sqlite3.Row
        self.drawer_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drawers (
                name TEXT PRIMARY KEY
            )
            """
        )
        self.drawers = {}
        for row in self.drawer_conn.execute("SELECT name FROM drawers"):
            self.drawers[row["name"]] = {
                "open": False,
                "balance": 0.0,
                "opening_balance": 0.0,
                "opened_by": None,
                "reconciled": True,
            }
        if not self.drawers:
            self.add_drawer("Schublade 1")
            self.add_drawer("Schublade 2")

        self.current_drawer: Optional[str] = None
        self.day_closed = True
        self.safe_balance = 0.0
        self.safe_journal: List[dict] = []
        self.reconcile_journal: List[dict] = []
        self.safe_reconciled = True

    def add_product(self, sku, name, price, stock, min_age=None, tax_rate: float = 0.0):
        if not (sku.isdigit() and 1 <= len(sku) <= 3):
            raise ValueError("Artikelnummer muss 1 bis 3 Stellen haben.")
        if sku in self.catalog:
            raise ValueError("Artikelnummer bereits vergeben.")
        if price != round(price, 2):
            raise ValueError("Preis darf nur zwei Nachkommastellen haben.")
        if tax_rate not in self.tax_rates:
            raise ValueError("Steuersatz nicht angelegt.")
        self.catalog[sku] = Product(sku, name, price, stock, min_age, tax_rate)
        self.inventory_conn.execute(
            "INSERT OR REPLACE INTO products(sku,name,price,stock,min_age,tax_rate) VALUES(?,?,?,?,?,?)",
            (sku, name, price, stock, min_age, tax_rate),
        )
        self.inventory_conn.commit()

    def update_product(
        self, sku, name=None, price=None, stock=None, min_age=None, tax_rate=None
    ):
        product = self.catalog.get(sku)
        if not product:
            raise ValueError("Produkt nicht gefunden.")
        if name is not None:
            product.name = name
        if price is not None:
            if price != round(price, 2):
                raise ValueError("Preis darf nur zwei Nachkommastellen haben.")
            product.price = price
        if stock is not None:
            product.stock = stock
        if min_age is not None:
            product.min_age = min_age
        if tax_rate is not None:
            if tax_rate not in self.tax_rates:
                raise ValueError("Steuersatz nicht angelegt.")
            product.tax_rate = tax_rate
        if product.tax_rate not in self.tax_rates:
            raise ValueError("Steuersatz nicht angelegt.")
        if product.price != round(product.price, 2):
            raise ValueError("Preis darf nur zwei Nachkommastellen haben.")
        self.inventory_conn.execute(
            "UPDATE products SET name=?, price=?, stock=?, min_age=?, tax_rate=? WHERE sku=?",
            (
                product.name,
                product.price,
                product.stock,
                product.min_age,
                product.tax_rate,
                sku,
            ),
        )
        self.inventory_conn.commit()

    # ----- Kassierer-Stammdaten -----
    def add_cashier(self, personnel_number: str, pin: str, name: str, role: str = "Kassierer"):
        if personnel_number != "admin" and (
            not personnel_number.isdigit() or len(personnel_number) != 4
        ):
            raise ValueError("Personalnummer muss 4 Stellen haben.")
        if personnel_number in self.cashiers:
            raise ValueError("Personalnummer bereits vergeben.")
        self.cashiers[personnel_number] = Cashier(personnel_number, pin, name, role)
        self.cashier_conn.execute(
            "INSERT OR REPLACE INTO cashiers(personnel_number,pin,name,role) VALUES(?,?,?,?)",
            (personnel_number, pin, name, role),
        )
        self.cashier_conn.commit()

    def update_cashier(
        self,
        personnel_number: str,
        pin: Optional[str] = None,
        name: Optional[str] = None,
        role: Optional[str] = None,
    ):
        cashier = self.cashiers.get(personnel_number)
        if not cashier:
            raise ValueError("Kassierer nicht gefunden.")
        if pin is not None:
            cashier.pin = pin
        if name is not None:
            cashier.name = name
        if role is not None:
            cashier.role = role
        self.cashier_conn.execute(
            "UPDATE cashiers SET pin=?, name=?, role=? WHERE personnel_number=?",
            (cashier.pin, cashier.name, cashier.role, personnel_number),
        )
        self.cashier_conn.commit()

    def delete_cashier(self, personnel_number: str):
        if personnel_number == "admin":
            raise ValueError("Admin kann nicht gelöscht werden.")
        if personnel_number in self.cashiers:
            del self.cashiers[personnel_number]
            self.cashier_conn.execute(
                "DELETE FROM cashiers WHERE personnel_number=?",
                (personnel_number,),
            )
            self.cashier_conn.commit()

    # ----- Schubladen-Stammdaten -----
    def add_drawer(self, name: str):
        if name in self.drawers:
            raise ValueError("Schublade bereits vorhanden.")
        self.drawers[name] = {
            "open": False,
            "balance": 0.0,
            "opening_balance": 0.0,
            "opened_by": None,
            "reconciled": True,
        }
        self.drawer_conn.execute("INSERT OR IGNORE INTO drawers(name) VALUES(?)", (name,))
        self.drawer_conn.commit()

    def remove_drawer(self, name: str):
        info = self.drawers.get(name)
        if not info:
            raise ValueError("Unbekannte Schublade.")
        if info.get("open") or self.current_drawer == name:
            raise ValueError("Schublade ist in Benutzung.")
        del self.drawers[name]
        self.drawer_conn.execute("DELETE FROM drawers WHERE name=?", (name,))
        self.drawer_conn.commit()

    def restock(self, sku, quantity):
        product = self.catalog.get(sku)
        if not product:
            raise ValueError("Produkt nicht gefunden.")
        product.stock += quantity
        self.inventory_log.append({
            "action": "restock",
            "sku": product.sku,
            "quantity": quantity,
            "timestamp": datetime.now().isoformat(),
        })
        self.inventory_conn.execute(
            "UPDATE products SET stock=? WHERE sku=?",
            (product.stock, sku),
        )
        self.inventory_conn.commit()

    def set_stock(self, sku, count):
        product = self.catalog.get(sku)
        if not product:
            raise ValueError("Produkt nicht gefunden.")
        product.stock = count
        self.inventory_log.append({
            "action": "inventory",
            "sku": product.sku,
            "count": count,
            "timestamp": datetime.now().isoformat(),
        })
        self.inventory_conn.execute(
            "UPDATE products SET stock=? WHERE sku=?",
            (product.stock, sku),
        )
        self.inventory_conn.commit()

    def checkout(self, cart: List[Tuple[str, int]], cashier: Optional[str] = None):
        if not self.current_drawer or self.day_closed:
            raise ValueError("Tag wurde noch nicht gestartet.")
        receipt_items = []
        total_net = 0
        total_tax = 0
        total = 0
        for sku, quantity in cart:
            product = self.catalog.get(sku)
            if not product:
                raise ValueError("Produkt nicht gefunden.")
            if product.stock < quantity:
                raise ValueError("Nicht genug Bestand.")
            product.stock -= quantity
            self.inventory_conn.execute(
                "UPDATE products SET stock=? WHERE sku=?",
                (product.stock, sku),
            )
            item_net = product.price * quantity
            item_tax = item_net * (product.tax_rate / 100)
            item_total = item_net + item_tax
            receipt_items.append(
                {
                    "sku": product.sku,
                    "name": product.name,
                    "quantity": quantity,
                    "price": product.price,
                    "tax_rate": product.tax_rate,
                    "tax": item_tax,
                    "total": item_total,
                }
            )
            total_net += item_net
            total_tax += item_tax
            total += item_total
        self.inventory_conn.commit()
        receipt = {
            "items": receipt_items,
            "net": total_net,
            "tax": total_tax,
            "total": total,
            "timestamp": datetime.now().isoformat(),
        }
        if cashier:
            receipt["cashier"] = cashier
        self.receipts.append(receipt)
        self.save_receipts()
        # Kassenbestand erhöhen
        drawer = self.drawers[self.current_drawer]
        drawer["balance"] += total
        return receipt

    def save_receipts(self, path="receipts.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.receipts, f, indent=2, ensure_ascii=False)

    def save_inventory_log(self, path="inventory_log.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.inventory_log, f, indent=2, ensure_ascii=False)

    def add_tax_rate(self, rate: float):
        if rate not in self.tax_rates:
            self.tax_rates.append(rate)
            self.tax_conn.execute("INSERT OR IGNORE INTO tax_rates(rate) VALUES(?)", (rate,))
            self.tax_conn.commit()

    def delete_tax_rate(self, rate: float):
        if rate in self.tax_rates:
            self.tax_rates.remove(rate)
            self.tax_conn.execute("DELETE FROM tax_rates WHERE rate=?", (rate,))
            self.tax_conn.commit()

    def daily_summary(self):
        net = sum(r.get("net", 0) for r in self.receipts)
        tax = sum(r.get("tax", 0) for r in self.receipts)
        total = sum(r.get("total", 0) for r in self.receipts)
        return net, tax, total

    def tax_summary(self):
        summary = {rate: {"qty": 0, "net": 0.0, "tax": 0.0, "gross": 0.0} for rate in self.tax_rates}
        for receipt in self.receipts:
            for item in receipt["items"]:
                rate = item.get("tax_rate", 0.0)
                data = summary.setdefault(rate, {"qty": 0, "net": 0.0, "tax": 0.0, "gross": 0.0})
                data["qty"] += item["quantity"]
                net = item["price"] * item["quantity"]
                data["net"] += net
                data["tax"] += item.get("tax", 0.0)
                data["gross"] += item.get("total", net)
        return summary

    def save_daily_close_journal(self, path="daily_close_journal.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.daily_close_journal, f, indent=2, ensure_ascii=False)

    def record_daily_close(self):
        net, tax, total = self.daily_summary()
        entry = {
            "number": len(self.daily_close_journal) + 1,
            "net": net,
            "tax": tax,
            "total": total,
            "timestamp": datetime.now().isoformat(),
        }
        self.daily_close_journal.append(entry)
        self.save_daily_close_journal()
        self.receipts.clear()
        self.save_receipts()
        return entry

    # --- Tagesbeginn, -abschluss und Tresor ---
    def start_day(self, drawer: str, opening_balance: float, cashier_id: str):
        info = self.drawers.get(drawer)
        if not info:
            raise ValueError("Unbekannte Schublade.")
        if info["open"]:
            if info.get("opened_by") and info.get("opened_by") != cashier_id:
                raise ValueError(
                    f"Schublade wird von {info.get('opened_by')} verwendet."
                )
            raise ValueError("Schublade bereits geöffnet.")
        if opening_balance > self.safe_balance:
            raise ValueError("Nicht genug Tresorbestand.")
        info["open"] = True
        info["balance"] = opening_balance
        info["opening_balance"] = opening_balance
        info["opened_by"] = cashier_id
        info["reconciled"] = False
        self.safe_balance -= opening_balance
        self.safe_reconciled = False
        self.current_drawer = drawer
        self.day_closed = False

    def deposit_to_safe(self, amount: float):
        if amount < 0:
            raise ValueError("Betrag muss positiv sein.")
        self.safe_balance += amount
        self.safe_reconciled = False
        self.safe_journal.append(
            {
                "type": "deposit",
                "amount": amount,
                "balance": self.safe_balance,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def withdraw_from_safe(self, amount: float):
        if amount < 0:
            raise ValueError("Betrag muss positiv sein.")
        if amount > self.safe_balance:
            raise ValueError("Nicht genug Tresorbestand.")
        self.safe_balance -= amount
        self.safe_reconciled = False
        self.safe_journal.append(
            {
                "type": "withdraw",
                "amount": amount,
                "balance": self.safe_balance,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def reconcile_safe(self, counted: float):
        diff = counted - self.safe_balance
        expected = self.safe_balance
        self.safe_balance = counted
        self.safe_reconciled = True
        entry = {
            "type": "reconcile",
            "amount": counted,
            "diff": diff,
            "balance": self.safe_balance,
            "timestamp": datetime.now().isoformat(),
        }
        self.safe_journal.append(entry)
        self.reconcile_journal.append(
            {
                "drawer": "Tresor",
                "expected": expected,
                "counted": counted,
                "diff": diff,
                "timestamp": entry["timestamp"],
            }
        )
        return entry

    def save_safe_journal(self, path="safe_journal.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.safe_journal, f, indent=2, ensure_ascii=False)

    def correct_safe_entry(self, index: int, new_amount: float):
        if index < 0 or index >= len(self.safe_journal):
            raise ValueError("Ungültiger Buchungsindex.")
        original = self.safe_journal[index]
        diff = new_amount - original.get("amount", 0.0)
        self.safe_balance += diff
        timestamp = datetime.now().isoformat()
        correction = {
            "type": "Korrektur",
            "amount": diff,
            "balance": self.safe_balance,
            "timestamp": timestamp,
            "correction_of": index,
        }
        self.safe_journal.append(correction)
        before = self.safe_balance - diff
        self.reconcile_journal.append(
            {
                "drawer": "Tresor",
                "expected": before,
                "counted": self.safe_balance,
                "diff": diff,
                "timestamp": timestamp,
                "correction_of": index,
            }
        )

    def record_drawer_reconcile(self, drawer: str, expected: float, counted: float):
        self.deposit_to_safe(counted)
        info = self.drawers.get(drawer)
        if info:
            info["balance"] = 0.0
            info["open"] = False
            info["opened_by"] = None
            info["opening_balance"] = 0.0
            info["reconciled"] = True
            if drawer == self.current_drawer:
                self.current_drawer = None
        entry = {
            "drawer": drawer,
            "expected": expected,
            "counted": counted,
            "diff": counted - expected,
            "timestamp": datetime.now().isoformat(),
        }
        self.reconcile_journal.append(entry)
        return entry

    def save_reconcile_journal(self, path="reconcile_journal.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.reconcile_journal, f, indent=2, ensure_ascii=False)

    def correct_reconcile_entry(self, index: int, new_counted: float):
        if index < 0 or index >= len(self.reconcile_journal):
            raise ValueError("Ungültiger Buchungsindex.")
        original = self.reconcile_journal[index]
        if original.get("drawer") == "Tresor":
            raise ValueError("Tresor-Einträge über correct_safe_entry korrigieren.")
        diff = new_counted - original.get("counted", 0.0)
        timestamp = datetime.now().isoformat()
        self.safe_balance += diff
        self.safe_journal.append(
            {
                "type": "Korrektur",
                "amount": diff,
                "balance": self.safe_balance,
                "timestamp": timestamp,
                "correction_of": index,
            }
        )
        self.reconcile_journal.append(
            {
                "drawer": original.get("drawer"),
                "expected": original.get("counted", 0.0),
                "counted": new_counted,
                "diff": diff,
                "timestamp": timestamp,
                "correction_of": index,
            }
        )

    def can_daily_close(self) -> bool:
        return all(info.get("reconciled") for info in self.drawers.values()) and self.safe_reconciled


class InventoryFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.on_back = on_back
        ttk.Label(self, text="Warenwirtschaft", style="Header.TLabel").pack(pady=(10, 0))

        self.tree = ttk.Treeview(
            self, columns=("name", "price", "stock", "tax"), show="headings", height=8
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("price", text="Preis")
        self.tree.heading("stock", text="Bestand")
        self.tree.heading("tax", text="Steuer %")
        self.tree.column("name", width=160)
        self.tree.column("price", width=80, anchor=tk.E)
        self.tree.column("stock", width=80, anchor=tk.E)
        self.tree.column("tax", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Artikel hinzufügen", command=self.add_product_dialog).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Artikel bearbeiten", command=self.update_product_dialog).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Wareneingang", command=self.restock_dialog).grid(row=0, column=2, padx=5)
        ttk.Button(btn, text="Inventur", command=self.inventory_dialog).grid(row=0, column=3, padx=5)
        ttk.Button(btn, text="Kassenzettel speichern", command=self.save_receipts).grid(row=0, column=4, padx=5)
        ttk.Button(btn, text="Warenlog speichern", command=self.save_inventory_log).grid(row=0, column=5, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=6, padx=5)

        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for prod in self.cr.catalog.values():
            self.tree.insert(
                "",
                tk.END,
                iid=prod.sku,
                values=(prod.name, f"{prod.price:.2f}", prod.stock, f"{prod.tax_rate:.2f}"),
            )

    def add_product_dialog(self):
        try:
            sku = simpledialog.askstring("SKU", "Artikelnummer:")
            if sku is None:
                return
            name = simpledialog.askstring("Name", "Artikelname:")
            price = float(simpledialog.askstring("Preis", "Preis:"))
            stock = int(simpledialog.askstring("Bestand", "Bestand:"))
            age_str = simpledialog.askstring(
                "Mindestalter", "Mindestalter (0,6,12,16,18; leer für keines):"
            )
            min_age = int(age_str) if age_str else None
            if min_age is not None and min_age not in AGE_CATEGORIES:
                raise ValueError("Ungültige Alterskategorie.")
            tax_str = simpledialog.askstring(
                "Steuersatz",
                f"Steuersatz in % ({', '.join(map(str, self.cr.tax_rates))}):",
            )
            tax_rate = float(tax_str) if tax_str else 0.0
            self.cr.add_product(sku, name, price, stock, min_age, tax_rate)
            self.refresh_tree()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def update_product_dialog(self):
        sku = self.tree.focus()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte einen Artikel auswählen.")
            return
        product = self.cr.catalog[sku]
        try:
            name = simpledialog.askstring("Name", "Neuer Name (leer für unverändert):") or None
            price_str = simpledialog.askstring("Preis", "Neuer Preis (leer für unverändert):")
            price = float(price_str) if price_str else None
            stock_str = simpledialog.askstring("Bestand", "Neuer Bestand (leer für unverändert):")
            stock = int(stock_str) if stock_str else None
            age_str = simpledialog.askstring(
                "Mindestalter",
                "Neues Mindestalter (0,6,12,16,18; leer für unverändert):",
            )
            min_age = int(age_str) if age_str else None
            if min_age is not None and min_age not in AGE_CATEGORIES:
                raise ValueError("Ungültige Alterskategorie.")
            tax_str = simpledialog.askstring(
                "Steuersatz", "Neuer Steuersatz in % (leer für unverändert):"
            )
            tax_rate = float(tax_str) if tax_str else None
            self.cr.update_product(sku, name, price, stock, min_age, tax_rate)
            self.refresh_tree()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def restock_dialog(self):
        sku = self.tree.focus()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte einen Artikel auswählen.")
            return
        try:
            qty = int(simpledialog.askstring("Wareneingang", "Menge:"))
            self.cr.restock(sku, qty)
            self.refresh_tree()
            if self.settings.get("auto_save_logs"):
                self.cr.save_inventory_log()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def inventory_dialog(self):
        sku = self.tree.focus()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte einen Artikel auswählen.")
            return
        try:
            count = int(simpledialog.askstring("Inventur", "Gezählter Bestand:") or "0")
            self.cr.set_stock(sku, count)
            self.refresh_tree()
            if self.settings.get("auto_save_logs"):
                self.cr.save_inventory_log()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def show_inventory_log(self):
        if not self.cr.inventory_log:
            messagebox.showinfo("Warenlog", "Keine Einträge vorhanden.")
            return
        lines = "\n".join(str(e) for e in self.cr.inventory_log)
        messagebox.showinfo("Warenlog", lines)

    def save_receipts(self):
        try:
            filename = simpledialog.askstring("Speichern", "Dateiname:", initialvalue="receipts.json")
            if filename:
                self.cr.save_receipts(filename)
                messagebox.showinfo("Info", "Kassenzettel gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def save_inventory_log(self):
        try:
            filename = simpledialog.askstring("Speichern", "Dateiname:", initialvalue="inventory_log.json")
            if filename:
                self.cr.save_inventory_log(filename)
                messagebox.showinfo("Info", "Warenlog gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class CashierAdminFrame(ttk.Frame):
    def __init__(
        self,
        parent,
        cr: CashRegister,
        on_back: Callable[[], None],
        allowed_roles: Tuple[str, ...] = ROLE_CHOICES,
    ):
        super().__init__(parent)
        self.cr = cr
        self.on_back = on_back
        self.allowed_roles = allowed_roles
        ttk.Label(self, text="Mitarbeiterstamm", style="Header.TLabel").pack(pady=(10, 0))

        self.tree = ttk.Treeview(
            self,
            columns=("pn", "name", "pin", "role"),
            show="headings",
            height=8,
        )
        self.tree.heading("pn", text="Personalnummer")
        self.tree.heading("name", text="Name")
        self.tree.heading("pin", text="PIN")
        self.tree.heading("role", text="Rolle")
        self.tree.column("pn", width=120)
        self.tree.column("name", width=140)
        self.tree.column("pin", width=80)
        self.tree.column("role", width=100)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Hinzufügen", command=self.add_cashier_dialog).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Bearbeiten", command=self.edit_cashier_dialog).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Löschen", command=self.delete_cashier).grid(row=0, column=2, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=3, padx=5)

        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for pn, cashier in self.cr.cashiers.items():
            self.tree.insert(
                "",
                tk.END,
                iid=pn,
                values=(pn, cashier.name, cashier.pin, cashier.role),
            )

    def add_cashier_dialog(self):
        win = tk.Toplevel(self)
        win.title("Mitarbeiter hinzufügen")

        ttk.Label(win, text="Personalnummer:").grid(row=0, column=0, sticky=tk.E, pady=5, padx=5)
        pn_var = tk.StringVar()
        ttk.Entry(win, textvariable=pn_var).grid(row=0, column=1, pady=5, padx=5)

        ttk.Label(win, text="Name:").grid(row=1, column=0, sticky=tk.E, pady=5, padx=5)
        name_var = tk.StringVar()
        ttk.Entry(win, textvariable=name_var).grid(row=1, column=1, pady=5, padx=5)

        ttk.Label(win, text="PIN:").grid(row=2, column=0, sticky=tk.E, pady=5, padx=5)
        pin_var = tk.StringVar()
        ttk.Entry(win, textvariable=pin_var).grid(row=2, column=1, pady=5, padx=5)

        ttk.Label(win, text="Rolle:").grid(row=3, column=0, sticky=tk.E, pady=5, padx=5)
        role_var = tk.StringVar(value=self.allowed_roles[0])
        ttk.Combobox(
            win,
            textvariable=role_var,
            values=self.allowed_roles,
            state="readonly",
        ).grid(row=3, column=1, pady=5, padx=5)

        def save():
            try:
                self.cr.add_cashier(pn_var.get(), pin_var.get(), name_var.get(), role_var.get())
                self.refresh_tree()
                win.destroy()
            except Exception as e:
                messagebox.showerror("Fehler", str(e))

        ttk.Button(win, text="Speichern", command=save).grid(row=4, column=0, columnspan=2, pady=10)

    def edit_cashier_dialog(self):
        pn = self.tree.focus()
        if not pn:
            messagebox.showinfo("Hinweis", "Bitte einen Mitarbeiter auswählen.")
            return
        cashier = self.cr.cashiers[pn]
        if cashier.role not in self.allowed_roles and self.allowed_roles != ROLE_CHOICES:
            messagebox.showerror("Fehler", "Keine Berechtigung zum Bearbeiten.")
            return
        win = tk.Toplevel(self)
        win.title("Mitarbeiter bearbeiten")

        ttk.Label(win, text="Personalnummer:").grid(row=0, column=0, sticky=tk.E, pady=5, padx=5)
        ttk.Label(win, text=pn).grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(win, text="Name:").grid(row=1, column=0, sticky=tk.E, pady=5, padx=5)
        name_var = tk.StringVar(value=cashier.name)
        ttk.Entry(win, textvariable=name_var).grid(row=1, column=1, pady=5, padx=5)

        ttk.Label(win, text="PIN:").grid(row=2, column=0, sticky=tk.E, pady=5, padx=5)
        pin_var = tk.StringVar(value=cashier.pin)
        ttk.Entry(win, textvariable=pin_var).grid(row=2, column=1, pady=5, padx=5)

        ttk.Label(win, text="Rolle:").grid(row=3, column=0, sticky=tk.E, pady=5, padx=5)
        role_var = tk.StringVar(value=cashier.role)
        ttk.Combobox(
            win,
            textvariable=role_var,
            values=self.allowed_roles,
            state="readonly",
        ).grid(row=3, column=1, pady=5, padx=5)

        def save():
            try:
                self.cr.update_cashier(pn, pin_var.get(), name_var.get(), role_var.get())
                self.refresh_tree()
                win.destroy()
            except Exception as e:
                messagebox.showerror("Fehler", str(e))

        ttk.Button(win, text="Speichern", command=save).grid(row=4, column=0, columnspan=2, pady=10)

    def delete_cashier(self):
        pn = self.tree.focus()
        if not pn:
            messagebox.showinfo("Hinweis", "Bitte einen Mitarbeiter auswählen.")
            return
        cashier = self.cr.cashiers[pn]
        if cashier.role not in self.allowed_roles and self.allowed_roles != ROLE_CHOICES:
            messagebox.showerror("Fehler", "Keine Berechtigung zum Löschen.")
            return
        if messagebox.askyesno("Löschen", "Mitarbeiter wirklich löschen?"):
            try:
                self.cr.delete_cashier(pn)
                self.refresh_tree()
            except ValueError as e:
                messagebox.showerror("Fehler", str(e))


class DrawerAdminFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.on_back = on_back
        ttk.Label(self, text="Schubladenstamm", style="Header.TLabel").pack(pady=(10, 0))

        self.tree = ttk.Treeview(self, columns=("name",), show="headings", height=8)
        self.tree.heading("name", text="Name")
        self.tree.column("name", width=200)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Hinzufügen", command=self.add_drawer).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Löschen", command=self.delete_drawer).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=2, padx=5)

        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for name in sorted(self.cr.drawers.keys()):
            self.tree.insert("", tk.END, iid=name, values=(name,))

    def add_drawer(self):
        name = simpledialog.askstring("Schublade", "Name:")
        if not name:
            return
        try:
            self.cr.add_drawer(name)
            self.refresh_tree()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def delete_drawer(self):
        name = self.tree.focus()
        if not name:
            messagebox.showinfo("Hinweis", "Bitte eine Schublade auswählen.")
            return
        if messagebox.askyesno("Löschen", "Schublade wirklich löschen?"):
            try:
                self.cr.remove_drawer(name)
                self.refresh_tree()
            except Exception as e:
                messagebox.showerror("Fehler", str(e))


class TaxAdminFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.on_back = on_back
        ttk.Label(self, text="Steuerverwaltung", style="Header.TLabel").pack(pady=(10, 0))

        self.tree = ttk.Treeview(
            self,
            columns=("rate", "qty", "net", "tax", "gross"),
            show="headings",
            height=8,
        )
        self.tree.heading("rate", text="Steuersatz (%)")
        self.tree.heading("qty", text="Abverkauf")
        self.tree.heading("net", text="Netto")
        self.tree.heading("tax", text="Steuer")
        self.tree.heading("gross", text="Brutto")
        self.tree.column("rate", width=100, anchor=tk.E)
        self.tree.column("qty", width=100, anchor=tk.E)
        self.tree.column("net", width=100, anchor=tk.E)
        self.tree.column("tax", width=100, anchor=tk.E)
        self.tree.column("gross", width=100, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Hinzufügen", command=self.add_rate_dialog).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Löschen", command=self.delete_rate).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=2, padx=5)

        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        summary = self.cr.tax_summary()
        for rate in sorted(self.cr.tax_rates):
            data = summary.get(rate, {"qty": 0, "net": 0.0, "tax": 0.0, "gross": 0.0})
            self.tree.insert(
                "",
                tk.END,
                iid=str(rate),
                values=(
                    f"{rate:.2f}",
                    data["qty"],
                    f"{data['net']:.2f}",
                    f"{data['tax']:.2f}",
                    f"{data['gross']:.2f}",
                ),
            )

    def add_rate_dialog(self):
        try:
            rate_str = simpledialog.askstring("Steuersatz", "Steuersatz in %:")
            if rate_str is None:
                return
            rate = float(rate_str)
            self.cr.add_tax_rate(rate)
            self.refresh_tree()
        except ValueError:
            messagebox.showerror("Fehler", "Ungültiger Steuersatz.")

    def delete_rate(self):
        sel = self.tree.focus()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte einen Steuersatz auswählen.")
            return
        rate = float(sel)
        self.cr.delete_tax_rate(rate)
        self.refresh_tree()


class StartDayFrame(ttk.Frame):
    def __init__(
        self,
        parent,
        cr: CashRegister,
        cashier: Cashier,
        on_started: Callable[[], None],
        on_cancel: Callable[[], None],
    ):
        super().__init__(parent)
        self.cr = cr
        self.cashier = cashier
        self.on_started = on_started
        self.on_cancel = on_cancel
        ttk.Label(self, text="Tagesbeginn", style="Header.TLabel").pack(pady=(10, 0))
        form = ttk.Frame(self)
        form.pack(pady=10)
        ttk.Label(form, text="Kassenschublade:").grid(row=0, column=0, sticky=tk.E, padx=5, pady=5)
        self.drawer_var = tk.StringVar()
        drawers = list(self.cr.drawers.keys())
        if drawers:
            self.drawer_var.set(drawers[0])
        ttk.Combobox(form, textvariable=self.drawer_var, values=drawers, state="readonly").grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(form, text="Anfangsbestand:").grid(row=1, column=0, sticky=tk.E, padx=5, pady=5)
        self.balance_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.balance_var).grid(row=1, column=1, padx=5, pady=5)
        btn = ttk.Frame(self)
        btn.pack(pady=10)
        ttk.Button(btn, text="Start", command=self.start).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Abbrechen", command=self.on_cancel).grid(row=0, column=1, padx=5)

    def start(self):
        try:
            bal = float(self.balance_var.get())
            self.cr.start_day(
                self.drawer_var.get(), bal, self.cashier.personnel_number
            )
            if self.on_started:
                self.on_started()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class CashierFrame(ttk.Frame):
    """Kassieroberfläche mit Kassenbons und Zahlpad wie in modernen POS-Systemen."""

    def __init__(
        self,
        parent,
        cr: CashRegister,
        cashier: Cashier,
        settings: dict,
        on_back: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.cr = cr
        self.cashier = cashier
        self.settings = settings
        self.currency = self.settings.get("currency", "€")
        self.on_back = on_back

        ttk.Label(self, text=f"Kassierer - {cashier.name}", style="Header.TLabel").pack(pady=(10, 0))
        # Warenkorb als Mapping SKU -> Menge
        self.cart = {}
        self.product_list_frame = None
        self.product_list_tree = None

        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # Linke Seite: Kassenzettel
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.receipt_tree = ttk.Treeview(
            left,
            columns=("name", "qty", "price", "total"),
            show="headings",
            height=15,
        )
        self.receipt_tree.heading("name", text="Artikel")
        self.receipt_tree.heading("qty", text="Anz.")
        self.receipt_tree.heading("price", text="Preis")
        self.receipt_tree.heading("total", text="Summe")
        self.receipt_tree.column("name", width=180)
        self.receipt_tree.column("qty", width=50, anchor=tk.E)
        self.receipt_tree.column("price", width=80, anchor=tk.E)
        self.receipt_tree.column("total", width=80, anchor=tk.E)
        self.receipt_tree.pack(fill=tk.BOTH, expand=True)

        ttk.Label(left, text="TOTAL", style="Header.TLabel").pack(anchor=tk.E, pady=(10, 0))
        self.total_var = tk.StringVar(value=f"0.00 {self.currency}")
        ttk.Label(left, textvariable=self.total_var, font=("Segoe UI", 24)).pack(anchor=tk.E)

        self.receipt_output = tk.Text(left, width=40, height=8, state=tk.DISABLED)
        self.receipt_output.pack(fill=tk.X, pady=(10, 0))

        # Rechte Seite: Eingabefelder und Zahlpad
        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, padx=(10, 0))

        entry = ttk.Frame(right)
        entry.pack(pady=5)
        ttk.Label(entry, text="SKU/EAN").grid(row=0, column=0, sticky=tk.W)
        self.sku_entry = ttk.Entry(entry, width=15)
        self.sku_entry.grid(row=1, column=0, padx=5, pady=(0, 5))
        ttk.Label(entry, text="Menge").grid(row=2, column=0, sticky=tk.W)
        self.qty_entry = ttk.Entry(entry, width=15)
        self.qty_entry.insert(0, "1")
        self.qty_entry.grid(row=3, column=0, padx=5, pady=(0, 5))
        self.active_entry = self.sku_entry
        self.sku_entry.bind("<FocusIn>", lambda e: self.set_active_entry(self.sku_entry))
        self.qty_entry.bind("<FocusIn>", lambda e: self.set_active_entry(self.qty_entry))

        keypad = ttk.Frame(right)
        keypad.pack()
        buttons = [
            ("7", 0, 0),
            ("8", 0, 1),
            ("9", 0, 2),
            ("4", 1, 0),
            ("5", 1, 1),
            ("6", 1, 2),
            ("1", 2, 0),
            ("2", 2, 1),
            ("3", 2, 2),
            ("0", 3, 1),
        ]
        for text, r, c in buttons:
            ttk.Button(keypad, text=text, command=lambda t=text: self.keypad_input(t)).grid(
                row=r, column=c, padx=3, pady=3, ipadx=10, ipady=10
            )
        ttk.Button(keypad, text="Clear", command=self.clear_entry).grid(
            row=3, column=0, padx=3, pady=3, ipadx=10, ipady=10
        )
        ttk.Button(keypad, text="Add", command=self.add_item_from_entries).grid(
            row=3, column=2, padx=3, pady=3, ipadx=10, ipady=10
        )

        pay = ttk.Frame(right)
        pay.pack(pady=10)
        ttk.Button(pay, text="Cancel", command=self.cancel_sale).grid(
            row=0, column=0, padx=5, pady=5
        )
        ttk.Button(pay, text="Finalize", command=self.finalize_sale).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(pay, text="Zeile löschen", command=self.remove_selected_item).grid(
            row=0, column=2, padx=5, pady=5
        )
        ttk.Button(pay, text="Artikelliste", command=self.open_product_list).grid(
            row=1, column=0, columnspan=3, pady=5
        )
        if self.on_back:
            ttk.Button(pay, text="Zurück", command=self.on_back).grid(
                row=2, column=0, columnspan=3, pady=5
            )

        self.sku_entry.focus()

    # --- Zahlpad-Helfer ---
    def set_active_entry(self, entry: tk.Entry):
        self.active_entry = entry

    def keypad_input(self, char: str):
        if self.active_entry:
            self.active_entry.insert(tk.END, char)
            self.active_entry.focus_set()

    def clear_entry(self):
        if self.active_entry:
            self.active_entry.delete(0, tk.END)
            self.active_entry.focus_set()

    # --- Warenkorb-Logik ---
    def add_item_by_sku(self, sku: str, qty: int = 1) -> bool:
        product = self.cr.catalog.get(sku)
        if not product:
            messagebox.showerror("Fehler", "Produkt nicht gefunden.")
            return False
        if product.min_age:
            age_ok = messagebox.askyesno(
                "Alterskontrolle", f"Kunde über {product.min_age}?",
            )
            if not age_ok:
                messagebox.showerror("Fehler", "Altersprüfung fehlgeschlagen.")
                return False
        current = self.cart.get(sku, 0)
        if product.stock < current + qty:
            messagebox.showerror("Fehler", "Nicht genug Bestand.")
            return False
        self.cart[sku] = current + qty
        self.refresh_receipt()
        return True

    def add_item_from_entries(self):
        sku = self.sku_entry.get()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte SKU eingeben.")
            return
        try:
            qty = int(self.qty_entry.get() or "1")
        except ValueError:
            messagebox.showerror("Fehler", "Ungültige Menge.")
            return
        if self.add_item_by_sku(sku, qty):
            self.sku_entry.delete(0, tk.END)
            self.qty_entry.delete(0, tk.END)
            self.qty_entry.insert(0, "1")

    def refresh_receipt(self):
        self.receipt_tree.delete(*self.receipt_tree.get_children())
        total = 0
        for sku, qty in self.cart.items():
            prod = self.cr.catalog[sku]
            unit_price = prod.price * (1 + prod.tax_rate / 100)
            item_total = unit_price * qty
            total += item_total
            self.receipt_tree.insert(
                "",
                tk.END,
                iid=sku,
                values=
                (
                    prod.name,
                    qty,
                    f"{unit_price:.2f} {self.currency}",
                    f"{item_total:.2f} {self.currency}",
                ),
            )
        self.total_var.set(f"{total:.2f} {self.currency}")

    def cancel_sale(self):
        self.cart.clear()
        self.refresh_receipt()

    def finalize_sale(self):
        if not self.cart:
            messagebox.showinfo("Hinweis", "Keine Artikel im Warenkorb.")
            return
        try:
            receipt = self.cr.checkout(
                list(self.cart.items()),
                f"{self.cashier.name} ({self.cashier.personnel_number})",
            )
            self.show_receipt(receipt)
            self.cancel_sale()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def remove_selected_item(self):
        sku = self.receipt_tree.focus()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte einen Artikel auswählen.")
            return
        if sku in self.cart:
            del self.cart[sku]
            self.refresh_receipt()

    # --- Artikelliste ---
    def open_product_list(self):
        if getattr(self, "product_list_frame", None):
            return
        frame = ttk.Frame(self, borderwidth=2, relief="raised")
        frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)
        ttk.Label(frame, text="Artikelliste", style="Header.TLabel").pack(pady=(10, 0))
        tree = ttk.Treeview(frame, columns=("name", "price", "stock"), show="headings")
        tree.heading("name", text="Artikel")
        tree.heading("price", text="Preis")
        tree.heading("stock", text="Bestand")
        tree.column("name", width=180)
        tree.column("price", width=80, anchor=tk.E)
        tree.column("stock", width=80, anchor=tk.E)
        for sku, prod in self.cr.catalog.items():
            tree.insert(
                "",
                tk.END,
                iid=sku,
                values=(
                    prod.name,
                    f"{prod.price:.2f} {self.currency}",
                    prod.stock,
                ),
            )
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        tree.bind("<Double-1>", lambda e: self.select_from_list())
        btn = ttk.Frame(frame)
        btn.pack(pady=5)
        ttk.Button(btn, text="Auswählen", command=self.select_from_list).grid(
            row=0, column=0, padx=5
        )
        ttk.Button(btn, text="Schließen", command=self.close_product_list).grid(
            row=0, column=1, padx=5
        )
        self.product_list_frame = frame
        self.product_list_tree = tree

    def close_product_list(self):
        if getattr(self, "product_list_frame", None):
            self.product_list_frame.destroy()
            self.product_list_frame = None
            self.product_list_tree = None
            self.sku_entry.focus_set()

    def select_from_list(self):
        if not getattr(self, "product_list_tree", None):
            return
        sku = self.product_list_tree.focus()
        if not sku:
            messagebox.showinfo("Hinweis", "Bitte einen Artikel auswählen.")
            return
        if self.add_item_by_sku(sku):
            self.close_product_list()

    def show_receipt(self, receipt):
        text = format_receipt_text(
            receipt,
            self.settings.get("store_name", "Kassensystem"),
            self.currency,
        )
        self.receipt_output.config(state=tk.NORMAL)
        self.receipt_output.delete("1.0", tk.END)
        self.receipt_output.insert(tk.END, text)
        self.receipt_output.config(state=tk.DISABLED)


class DailyCloseFrame(ttk.Frame):
    def __init__(
        self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]
    ):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back
        ttk.Label(self, text="Tagesabschluss", style="Header.TLabel").pack(pady=(10, 0))
        net, tax, total = self.cr.daily_summary()
        text = tk.Text(self, width=40, height=6, borderwidth=0, highlightthickness=0)
        text.pack(padx=10, pady=10)
        text.insert(tk.END, f"Netto: {net:.2f} {self.currency}\n")
        text.insert(tk.END, f"Steuer: {tax:.2f} {self.currency}\n")
        text.insert(tk.END, f"Gesamt: {total:.2f} {self.currency}\n")
        text.insert(tk.END, f"Tresorbestand: {self.cr.safe_balance:.2f} {self.currency}\n")
        text.config(state=tk.DISABLED)

        self.status_tree = ttk.Treeview(
            self, columns=("quelle", "status"), show="headings", height=5
        )
        self.status_tree.heading("quelle", text="Quelle")
        self.status_tree.heading("status", text="Status")
        self.status_tree.column("quelle", width=150)
        self.status_tree.column("status", width=100)
        self.status_tree.pack(padx=10, pady=5, fill=tk.X)

        self.finish_btn = ttk.Button(self, text="Abschließen", command=self.finish)
        self.finish_btn.pack(pady=5)
        ttk.Button(self, text="Zurück", command=self.on_back).pack(pady=5)

        self.refresh_status()

    def finish(self):
        if not self.cr.can_daily_close():
            messagebox.showerror(
                "Fehler", "Nicht alle Kassen oder der Tresor wurden abgerechnet."
            )
            return
        if not messagebox.askyesno(
            "Bestätigung", "Tagesabschluss wirklich durchführen?"
        ):
            return
        entry = self.cr.record_daily_close()
        self.cr.day_closed = True
        if self.settings.get("auto_save_logs"):
            self.cr.save_reconcile_journal()
            self.cr.save_safe_journal()
        messagebox.showinfo(
            "Info", f"Tagesabschluss Nr. {entry['number']} gebucht."
        )
        self.on_back()

    def refresh_status(self):
        for i in self.status_tree.get_children():
            self.status_tree.delete(i)
        for name, info in self.cr.drawers.items():
            status = "Gezählt" if info.get("reconciled") else "Offen"
            self.status_tree.insert("", tk.END, values=(name, status))
        status = "Gezählt" if self.cr.safe_reconciled else "Offen"
        self.status_tree.insert("", tk.END, values=("Tresor", status))
        if self.cr.can_daily_close():
            self.finish_btn.state(["!disabled"])
        else:
            self.finish_btn.state(["disabled"])


class CashJournalFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.currency = settings.get("currency", "€")
        self.store_name = settings.get("store_name", "Kassensystem")
        self.on_back = on_back
        ttk.Label(self, text="Kassenjournal", style="Header.TLabel").pack(pady=(10, 0))
        self.tree = ttk.Treeview(
            self, columns=("time", "cashier", "total"), show="headings", height=8
        )
        self.tree.heading("time", text="Zeit")
        self.tree.heading("cashier", text="Kassierer")
        self.tree.heading("total", text="Summe")
        self.tree.column("time", width=160)
        self.tree.column("cashier", width=120)
        self.tree.column("total", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)
        self.receipt_view = tk.Text(self, width=60, height=10, state=tk.DISABLED)
        self.receipt_view.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        ttk.Button(self, text="Zurück", command=self.on_back).pack(pady=5)
        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, r in enumerate(self.cr.receipts):
                self.tree.insert(
                    "",
                    tk.END,
                    iid=str(idx),
                    values=(
                        r.get("timestamp", ""),
                        r.get("cashier", ""),
                        f"{r.get('total', 0):.2f} {self.currency}",
                    ),
                )

    def show_selected(self, event=None):
        sel = self.tree.focus()
        if not sel:
            return
        receipt = self.cr.receipts[int(sel)]
        text = format_receipt_text(receipt, self.store_name, self.currency)
        self.receipt_view.config(state=tk.NORMAL)
        self.receipt_view.delete("1.0", tk.END)
        self.receipt_view.insert(tk.END, text)
        self.receipt_view.config(state=tk.DISABLED)


class InventoryJournalFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.on_back = on_back
        ttk.Label(self, text="Warenjournal", style="Header.TLabel").pack(pady=(10, 0))
        self.tree = ttk.Treeview(
            self,
            columns=("time", "action", "sku", "value"),
            show="headings",
            height=8,
        )
        self.tree.heading("time", text="Zeit")
        self.tree.heading("action", text="Aktion")
        self.tree.heading("sku", text="SKU")
        self.tree.heading("value", text="Wert")
        self.tree.column("time", width=160)
        self.tree.column("action", width=100)
        self.tree.column("sku", width=80)
        self.tree.column("value", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Speichern", command=self.save).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=1, padx=5)
        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for entry in self.cr.inventory_log:
            value = entry.get("quantity") or entry.get("count") or 0
            self.tree.insert(
                "",
                tk.END,
                values=(entry.get("timestamp", ""), entry.get("action", ""), entry.get("sku", ""), value),
            )

    def save(self):
        try:
            filename = simpledialog.askstring(
                "Speichern", "Dateiname:", initialvalue="inventory_log.json"
            )
            if filename:
                self.cr.save_inventory_log(filename)
                messagebox.showinfo("Info", "Warenjournal gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class SafeJournalFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back
        ttk.Label(self, text="Tresorjournal", style="Header.TLabel").pack(pady=(10, 0))
        self.tree = ttk.Treeview(
            self, columns=("time", "type", "amount", "balance"), show="headings", height=8
        )
        self.tree.heading("time", text="Zeit")
        self.tree.heading("type", text="Typ")
        self.tree.heading("amount", text="Betrag")
        self.tree.heading("balance", text="Bestand")
        self.tree.column("time", width=160)
        self.tree.column("type", width=100)
        self.tree.column("amount", width=80, anchor=tk.E)
        self.tree.column("balance", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Speichern", command=self.save).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Korrigieren", command=self.correct).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=2, padx=5)
        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, entry in enumerate(self.cr.safe_journal):
            self.tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    entry.get("timestamp", ""),
                    entry.get("type", ""),
                    f"{entry.get('amount', 0):.2f} {self.currency}",
                    f"{entry.get('balance', 0):.2f} {self.currency}",
                ),
            )

    def save(self):
        try:
            filename = simpledialog.askstring(
                "Speichern", "Dateiname:", initialvalue="safe_journal.json"
            )
            if filename:
                self.cr.save_safe_journal(filename)
                messagebox.showinfo("Info", "Tresorjournal gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def correct(self):
        sel = self.tree.focus()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte eine Buchung auswählen.")
            return
        idx = int(sel)
        new_amount = simpledialog.askfloat("Korrektur", "Neuer Betrag:")
        if new_amount is None:
            return
        try:
            self.cr.correct_safe_entry(idx, new_amount)
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_safe_journal()
                self.cr.save_reconcile_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class ReconcileJournalFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back
        ttk.Label(self, text="Abrechnungsjournal", style="Header.TLabel").pack(pady=(10, 0))
        self.tree = ttk.Treeview(
            self,
            columns=("time", "drawer", "expected", "counted", "diff"),
            show="headings",
            height=8,
        )
        self.tree.heading("time", text="Zeit")
        self.tree.heading("drawer", text="Schublade")
        self.tree.heading("expected", text="Erwartet")
        self.tree.heading("counted", text="Gezählt")
        self.tree.heading("diff", text="Differenz")
        self.tree.column("time", width=160)
        self.tree.column("drawer", width=120)
        self.tree.column("expected", width=80, anchor=tk.E)
        self.tree.column("counted", width=80, anchor=tk.E)
        self.tree.column("diff", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Speichern", command=self.save).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Korrigieren", command=self.correct).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=2, padx=5)
        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, entry in enumerate(self.cr.reconcile_journal):
            self.tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    entry.get("timestamp", ""),
                    entry.get("drawer", ""),
                    f"{entry.get('expected', 0):.2f} {self.currency}",
                    f"{entry.get('counted', 0):.2f} {self.currency}",
                    f"{entry.get('diff', 0):.2f} {self.currency}",
                ),
            )

    def save(self):
        try:
            filename = simpledialog.askstring(
                "Speichern", "Dateiname:", initialvalue="reconcile_journal.json"
            )
            if filename:
                self.cr.save_reconcile_journal(filename)
                messagebox.showinfo("Info", "Abrechnungsjournal gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def correct(self):
        sel = self.tree.focus()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte eine Buchung auswählen.")
            return
        idx = int(sel)
        new_counted = simpledialog.askfloat("Korrektur", "Neuer gezählter Bestand:")
        if new_counted is None:
            return
        try:
            self.cr.correct_reconcile_entry(idx, new_counted)
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_reconcile_journal()
                self.cr.save_safe_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class DailyCloseJournalFrame(ttk.Frame):
    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.currency = settings.get("currency", "€")
        self.store_name = settings.get("store_name", "Kassensystem")
        self.on_back = on_back
        ttk.Label(self, text="Tagesabschluss-Journal", style="Header.TLabel").pack(pady=(10, 0))
        self.tree = ttk.Treeview(
            self, columns=("number", "time", "total"), show="headings", height=8
        )
        self.tree.heading("number", text="Nr.")
        self.tree.heading("time", text="Zeit")
        self.tree.heading("total", text="Summe")
        self.tree.column("number", width=60, anchor=tk.E)
        self.tree.column("time", width=160)
        self.tree.column("total", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)
        self.text = tk.Text(self, width=60, height=10, state=tk.DISABLED)
        self.text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Speichern", command=self.save).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=1, padx=5)
        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for entry in self.cr.daily_close_journal:
            self.tree.insert(
                "",
                tk.END,
                iid=str(entry.get("number")),
                values=(
                    entry.get("number"),
                    entry.get("timestamp", ""),
                    f"{entry.get('total', 0):.2f} {self.currency}",
                ),
            )

    def show_selected(self, event=None):
        sel = self.tree.focus()
        if not sel:
            return
        entry = next(
            (e for e in self.cr.daily_close_journal if str(e.get("number")) == sel),
            None,
        )
        if not entry:
            return
        text = format_daily_close_text(entry, self.store_name, self.currency)
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, text)
        self.text.config(state=tk.DISABLED)

    def save(self):
        try:
            filename = simpledialog.askstring(
                "Speichern", "Dateiname:", initialvalue="daily_close_journal.json"
            )
            if filename:
                self.cr.save_daily_close_journal(filename)
                messagebox.showinfo("Info", "Tagesabschluss-Journal gespeichert.")
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class JournalMenuFrame(ttk.Frame):
    def __init__(
        self,
        parent,
        open_cash: Callable[[], None],
        open_inventory: Callable[[], None],
        open_safe: Callable[[], None],
        open_recon: Callable[[], None],
        open_dayclose: Callable[[], None],
        on_back: Callable[[], None],
    ):
        super().__init__(parent)
        ttk.Label(self, text="Journale", style="Header.TLabel").pack(pady=(10, 0))
        btn = ttk.Frame(self)
        btn.pack(pady=10)
        ttk.Button(btn, text="Kassenjournal", command=open_cash).grid(
            row=0, column=0, padx=5, pady=5
        )
        ttk.Button(btn, text="Warenjournal", command=open_inventory).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(btn, text="Tresorjournal", command=open_safe).grid(
            row=0, column=2, padx=5, pady=5
        )
        ttk.Button(btn, text="Abrechnungsjournal", command=open_recon).grid(
            row=0, column=3, padx=5, pady=5
        )
        ttk.Button(btn, text="Tagesabschluss", command=open_dayclose).grid(
            row=0, column=4, padx=5, pady=5
        )
        ttk.Button(self, text="Zurück", command=on_back).pack(pady=5)


class CashManagementFrame(ttk.Frame):
    """Button navigation for safe and drawer management."""

    def __init__(self, parent, cr: CashRegister, settings: dict, on_back: Callable[[], None]):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        nav = ttk.Frame(self)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 10), pady=10)
        ttk.Button(nav, text="Tresor", command=self.show_safe).pack(fill=tk.X, pady=(0, 5))
        ttk.Button(nav, text="Bediener", command=self.show_drawer).pack(fill=tk.X)

        self.content = ttk.Frame(self)
        self.content.grid(row=0, column=1, sticky="nsew", pady=10)

        ttk.Button(self, text="Zurück", command=self.on_back).grid(
            row=1, column=1, sticky="e", pady=5, padx=10
        )

        self.show_safe()

    def clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()

    def show_safe(self):
        self.clear_content()
        frame = SafeManagementFrame(
            self.content, self.cr, self.settings, on_back=self.on_back, show_back=False
        )
        frame.pack(fill=tk.BOTH, expand=True)

    def show_drawer(self):
        self.clear_content()
        frame = DrawerManagementFrame(
            self.content,
            self.cr,
            self.settings,
            on_back=self.on_back,
            with_nav=False,
            show_back=False,
        )
        frame.pack(fill=tk.BOTH, expand=True)


class SafeManagementFrame(ttk.Frame):
    def __init__(
        self,
        parent,
        cr: CashRegister,
        settings: dict,
        on_back: Callable[[], None],
        show_back: bool = True,
    ):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back
        ttk.Label(self, text="Tresor", style="Header.TLabel").pack(pady=(10, 0))
        ttk.Label(self, text="Tresorbestand:").pack(pady=(10, 0))
        self.balance_var = tk.StringVar()
        ttk.Label(self, textvariable=self.balance_var, font=("Segoe UI", 12, "bold")).pack()
        btn = ttk.Frame(self)
        btn.pack(pady=10)
        ttk.Button(btn, text="Einzahlen", command=self.deposit).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Auszahlen", command=self.withdraw).grid(row=0, column=1, padx=5)
        ttk.Button(btn, text="Abrechnen", command=self.reconcile).grid(row=0, column=2, padx=5)
        if show_back:
            ttk.Button(self, text="Zurück", command=self.on_back).pack(pady=5)
        self.refresh()

    def refresh(self):
        self.balance_var.set(f"{self.cr.safe_balance:.2f} {self.currency}")

    def deposit(self):
        amount = simpledialog.askfloat("Einzahlen", "Betrag:")
        if amount is None:
            return
        try:
            self.cr.deposit_to_safe(amount)
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_safe_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def withdraw(self):
        amount = simpledialog.askfloat("Auszahlen", "Betrag:")
        if amount is None:
            return
        try:
            self.cr.withdraw_from_safe(amount)
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_safe_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def reconcile(self):
        counted = simpledialog.askfloat("Tresorabrechnung", "Gezählter Bestand:")
        if counted is None:
            return
        try:
            entry = self.cr.reconcile_safe(counted)
            messagebox.showinfo(
                "Abrechnung",
                f"Differenz: {entry['diff']:.2f} {self.currency}",
            )
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_safe_journal()
                self.cr.save_reconcile_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class DrawerManagementFrame(ttk.Frame):
    """Manage drawer skimming with optional sidebar and detailed tables."""

    def __init__(
        self,
        parent,
        cr: CashRegister,
        settings: dict,
        on_back: Callable[[], None],
        with_nav: bool = True,
        show_back: bool = True,
    ):
        super().__init__(parent)
        self.cr = cr
        self.settings = settings
        self.currency = settings.get("currency", "€")
        self.on_back = on_back

        if with_nav:
            self.columnconfigure(1, weight=1)
            self.rowconfigure(0, weight=1)
            self.nav = ttk.Treeview(self, show="tree", selectmode="browse", height=4)
            self.nav.insert("", "end", "geld", text="Geldwirtschaft")
            self.nav.insert("geld", "end", "bed", text="Bediener")
            self.nav.insert("bed", "end", "absch", text="Abschöpfung erfassen")
            self.nav.selection_set("absch")
            self.nav.grid(row=0, column=0, sticky="ns", padx=(0, 10), pady=10)
            content = ttk.Frame(self)
            content.grid(row=0, column=1, sticky="nsew", pady=10)
        else:
            self.columnconfigure(0, weight=1)
            self.rowconfigure(0, weight=1)
            content = ttk.Frame(self)
            content.grid(row=0, column=0, sticky="nsew", pady=10)

        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)

        ttk.Label(content, text="Abschöpfung erfassen", style="Header.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        balance_cols = ("drawer", "balance")
        self.tree = ttk.Treeview(
            content, columns=balance_cols, show="headings", height=5
        )
        self.tree.heading("drawer", text="Schublade")
        self.tree.heading("balance", text=f"Saldo ({self.currency})")
        self.tree.column("drawer", width=150)
        self.tree.column("balance", width=120, anchor=tk.E)
        self.tree.grid(row=1, column=0, sticky="ew", pady=5)

        ttk.Label(content, text="Abrechnungen").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        log_cols = ("drawer", "expected", "counted", "diff")
        self.log_tree = ttk.Treeview(
            content, columns=log_cols, show="headings", height=5
        )
        for col, text in [
            ("drawer", "Schublade"),
            ("expected", f"Erwartet ({self.currency})"),
            ("counted", f"Gezählt ({self.currency})"),
            ("diff", f"Differenz ({self.currency})"),
        ]:
            self.log_tree.heading(col, text=text)
            width = 150 if col == "drawer" else 120
            anchor = tk.W if col == "drawer" else tk.E
            self.log_tree.column(col, width=width, anchor=anchor)
        self.log_tree.grid(row=3, column=0, sticky="nsew", pady=5)

        btn = ttk.Frame(content)
        btn.grid(row=4, column=0, sticky="e", pady=10)
        ttk.Button(btn, text="Abrechnen", command=self.reconcile).pack(side=tk.LEFT, padx=5)
        if show_back:
            ttk.Button(btn, text="Zurück", command=self.on_back).pack(side=tk.LEFT, padx=5)

        self.refresh()

    def refresh(self):
        # top table: current drawer balances
        for i in self.tree.get_children():
            self.tree.delete(i)
        for name, info in self.cr.drawers.items():
            self.tree.insert(
                "",
                tk.END,
                iid=name,
                values=(name, f"{info['balance']:.2f} {self.currency}"),
            )

        # bottom table: reconciliation log
        for i in self.log_tree.get_children():
            self.log_tree.delete(i)
        for entry in self.cr.reconcile_journal:
            if entry.get("drawer") and entry["drawer"] != "Tresor":
                self.log_tree.insert(
                    "",
                    tk.END,
                    values=(
                        entry["drawer"],
                        f"{entry['expected']:.2f} {self.currency}",
                        f"{entry['counted']:.2f} {self.currency}",
                        f"{entry['diff']:.2f} {self.currency}",
                    ),
                )

    def reconcile(self):
        drawer = self.tree.focus()
        if not drawer:
            messagebox.showinfo("Hinweis", "Bitte eine Schublade auswählen.")
            return
        expected = self.cr.drawers.get(drawer, {}).get("balance", 0.0)
        counted = simpledialog.askfloat("Kassenabrechnung", "Gezählter Bestand:")
        if counted is None:
            return
        try:
            self.cr.record_drawer_reconcile(drawer, expected, counted)
            self.refresh()
            if self.settings.get("auto_save_logs"):
                self.cr.save_reconcile_journal()
                self.cr.save_safe_journal()
        except Exception as e:
            messagebox.showerror("Fehler", str(e))


class TechnikFrame(ttk.Frame):
    def __init__(self, parent, app: "CashRegisterApp", on_back: Callable[[], None]):
        super().__init__(parent)
        self.app = app
        self.on_back = on_back
        ttk.Label(self, text="Technik", style="Header.TLabel").pack(pady=(10, 0))

        form = ttk.Frame(self)
        form.pack(pady=10)
        ttk.Label(form, text="Version:").grid(row=0, column=0, sticky=tk.E, pady=5, padx=5)
        self.version_var = tk.StringVar(value=self.app.settings.get("version", "1.0.0"))
        ttk.Entry(form, textvariable=self.version_var).grid(row=0, column=1, pady=5, padx=5)

        ttk.Label(form, text="Filialname:").grid(row=1, column=0, sticky=tk.E, pady=5, padx=5)
        self.store_var = tk.StringVar(
            value=self.app.settings.get("store_name", "Kassensystem")
        )
        ttk.Entry(form, textvariable=self.store_var).grid(row=1, column=1, pady=5, padx=5)

        ttk.Label(form, text="Währung:").grid(row=2, column=0, sticky=tk.E, pady=5, padx=5)
        self.currency_var = tk.StringVar(
            value=self.app.settings.get("currency", "€")
        )
        ttk.Entry(form, textvariable=self.currency_var, width=5).grid(
            row=2, column=1, pady=5, padx=5, sticky=tk.W
        )

        self.debug_var = tk.BooleanVar(value=self.app.settings.get("debug", False))
        ttk.Checkbutton(form, text="Debugmodus aktiv", variable=self.debug_var).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=5, padx=5
        )

        self.auto_save_receipts_var = tk.BooleanVar(
            value=self.app.settings.get("auto_save_receipts", False)
        )
        ttk.Checkbutton(
            form,
            text="Kassenzettel automatisch speichern",
            variable=self.auto_save_receipts_var,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=5, padx=5)

        self.auto_save_logs_var = tk.BooleanVar(
            value=self.app.settings.get("auto_save_logs", False)
        )
        ttk.Checkbutton(
            form,
            text="Journale automatisch speichern",
            variable=self.auto_save_logs_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=5, padx=5)

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 10))
        ttk.Button(btn, text="Speichern", command=self.save).grid(row=0, column=0, padx=5)
        ttk.Button(btn, text="Zurück", command=self.on_back).grid(row=0, column=1, padx=5)

    def save(self):
        self.app.settings["version"] = self.version_var.get()
        self.app.settings["store_name"] = self.store_var.get()
        self.app.settings["currency"] = self.currency_var.get()
        self.app.settings["debug"] = self.debug_var.get()
        self.app.settings["auto_save_receipts"] = self.auto_save_receipts_var.get()
        self.app.settings["auto_save_logs"] = self.auto_save_logs_var.get()
        save_settings(self.app.settings)
        messagebox.showinfo("Gespeichert", "Einstellungen gespeichert")


class CashRegisterApp:
    def __init__(self, root, mode: str = "full"):
        self.root = root
        self.root.title("Kassensystem")
        self.mode = mode  # "pos" for Kasse, "backoffice" for Verwaltung
        self.cr = CashRegister()
        self.current_cashier: Optional[Cashier] = None
        self.settings = load_settings()
        self.container = ttk.Frame(root)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.status_var = tk.StringVar()
        self.status_frame = ttk.Frame(root)
        self.status_frame.pack(side=tk.BOTTOM, anchor=tk.E, padx=10, pady=5)
        ttk.Label(self.status_frame, textvariable=self.status_var).pack(side=tk.LEFT)
        self.logout_btn = ttk.Button(self.status_frame, text="Abmelden", command=self.logout)
        self.current_frame: Optional[ttk.Frame] = None
        self.show_login()

    def update_status(self):
        name = self.current_cashier.name if self.current_cashier else ""
        self.status_var.set(f"Benutzer: {name}" if name else "")
        if name:
            if not self.logout_btn.winfo_ismapped():
                self.logout_btn.pack(side=tk.LEFT, padx=(5, 0))
        else:
            if self.logout_btn.winfo_ismapped():
                self.logout_btn.pack_forget()

    def show_login(self):
        self.update_status()
        frame = ttk.Frame(self.container, padding=20)
        ttk.Label(
            frame,
            text=self.settings.get("store_name", "Kassensystem"),
            style="Header.TLabel",
        ).grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(frame, text="Login").grid(row=1, column=0, columnspan=2, pady=(0, 10))
        ttk.Label(frame, text="Benutzer:").grid(row=2, column=0, sticky=tk.E, pady=5)
        users = [f"{pn} - {c.name}" for pn, c in self.cr.cashiers.items()]
        pn_var = tk.StringVar()
        pn_combo = ttk.Combobox(frame, textvariable=pn_var, values=users, state="readonly")
        pn_combo.grid(row=2, column=1, pady=5)
        ttk.Label(frame, text="PIN:").grid(row=3, column=0, sticky=tk.E, pady=5)
        pin_entry = ttk.Entry(frame, show="*")
        pin_entry.grid(row=3, column=1, pady=5)

        def attempt_login(event=None):
            selection = pn_var.get()
            pn = selection.split(" - ")[0] if selection else ""
            pin = pin_entry.get()
            cashier = self.cr.cashiers.get(pn)
            if cashier and cashier.pin == pin:
                if self.mode == "pos" and cashier.role not in (
                    "Admin",
                    "Kassierer",
                    "Filialleiter",
                ):
                    messagebox.showerror(
                        "Fehler", "Keine Berechtigung für das Kassenprogramm."
                    )
                    return
                if self.mode == "backoffice" and cashier.role not in (
                    "Admin",
                    "Lagerist",
                    "Filialleiter",
                    "Steuerberater",
                    "Techniker",
                ):
                    messagebox.showerror(
                        "Fehler", "Keine Berechtigung für das Backoffice."
                    )
                    return
                self.current_cashier = cashier
                self.update_status()
                if self.mode != "backoffice" and cashier.role == "Kassierer":
                    self.open_cashier()
                else:
                    self.show_menu()
            else:
                messagebox.showerror("Fehler", "Ungültige Personalnummer oder PIN")

        ttk.Button(frame, text="Anmelden", command=attempt_login).grid(
            row=4, column=0, columnspan=2, pady=10
        )
        ttk.Label(frame, text=f"Version {self.settings.get('version', '1.0.0')}").grid(
            row=5, column=0, columnspan=2, pady=(0, 10)
        )
        pn_combo.focus()
        self.switch_frame(frame)

    def show_menu(self):
        frame = ttk.Frame(self.container)
        ttk.Label(
            frame,
            text=self.settings.get("store_name", "Kassensystem"),
            style="Header.TLabel",
        ).pack(pady=(20, 10), anchor=tk.W)
        btn_frame = ttk.Frame(frame, padding=20)
        btn_frame.pack(anchor=tk.W)

        buttons: List[Tuple[str, Callable[[], None]]] = []
        role = self.current_cashier.role
        if self.mode in ("full", "pos"):
            if role in ("Admin", "Kassierer", "Filialleiter"):
                buttons.append(("Kasse", self.open_cashier))
            if role in ("Admin", "Kassierer", "Filialleiter"):
                buttons.append(("Tagesabschluss", self.open_daily_close))
        if self.mode in ("full", "backoffice"):
            if role in ("Admin", "Lagerist", "Filialleiter"):
                buttons.append(("Warenwirtschaft", self.open_inventory))
            if role in ("Admin", "Filialleiter"):
                buttons.append(("Stammdaten", self.open_master_data_menu))
            if role in ("Admin", "Steuerberater"):
                buttons.append(("Steuerverwaltung", self.open_tax_admin))
            if role in ("Admin", "Techniker"):
                buttons.append(("Technik", self.open_technik))
            if role in ("Admin", "Filialleiter"):
                buttons.append(("Geldwirtschaft", self.open_cash_management))
                buttons.append(("Journale", self.open_journal_menu))

        for text, cmd in buttons:
            ttk.Button(btn_frame, text=text, command=cmd, width=20).pack(
                fill=tk.X, pady=5, anchor=tk.W
            )
        self.switch_frame(frame)

    def logout(self):
        if (
            self.current_cashier
            and self.current_cashier.role in ("Admin", "Kassierer", "Filialleiter")
            and self.cr.current_drawer
            and not self.cr.day_closed
        ):
            if messagebox.askyesno(
                "Hinweis", "Tagesabschluss nicht durchgeführt. Jetzt durchführen?"
            ):
                self.open_daily_close()
                return
        self.current_cashier = None
        self.update_status()
        self.show_login()

    def open_cashier(self):
        if self.current_cashier and self.current_cashier.role in (
            "Admin",
            "Kassierer",
            "Filialleiter",
        ):
            if self.cr.current_drawer and not self.cr.day_closed:
                info = self.cr.drawers.get(self.cr.current_drawer, {})
                if info.get("opened_by") != self.current_cashier.personnel_number:
                    messagebox.showerror(
                        "Fehler",
                        f"Schublade wird von {info.get('opened_by')} verwendet.",
                    )
                    return
            if not self.cr.current_drawer or self.cr.day_closed:
                frame = StartDayFrame(
                    self.container,
                    self.cr,
                    self.current_cashier,
                    on_started=self.open_cashier,
                    on_cancel=self.logout if self.current_cashier.role == "Kassierer" else self.show_menu,
                )
                self.switch_frame(frame)
                return
            on_back = None if self.current_cashier.role == "Kassierer" else self.show_menu
            frame = CashierFrame(
                self.container,
                self.cr,
                self.current_cashier,
                self.settings,
                on_back=on_back,
            )
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung zum Kassieren.")

    def open_inventory(self):
        if self.current_cashier and self.current_cashier.role in (
            "Admin",
            "Lagerist",
            "Filialleiter",
        ):
            frame = InventoryFrame(self.container, self.cr, self.settings, on_back=self.show_menu)
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Warenwirtschaft.")

    def open_master_data_menu(self):
        frame = ttk.Frame(self.container, padding=20)
        ttk.Label(frame, text="Stammdaten", style="Header.TLabel").pack(pady=(10, 0))
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)

        role = self.current_cashier.role
        idx = 0
        if role in ("Admin", "Filialleiter"):
            ttk.Button(
                btn_frame,
                text="Mitarbeiterstamm",
                command=lambda: self.open_cashier_admin(on_back=self.open_master_data_menu),
                width=20,
            ).grid(row=0, column=idx, padx=10, pady=10)
            idx += 1
        if role == "Admin":
            ttk.Button(
                btn_frame,
                text="Schubladenstamm",
                command=lambda: self.open_drawer_admin(on_back=self.open_master_data_menu),
                width=20,
            ).grid(row=0, column=idx, padx=10, pady=10)

        ttk.Button(frame, text="Zurück", command=self.show_menu).pack(pady=10)
        self.switch_frame(frame)

    def open_cashier_admin(self, on_back=None):
        if self.current_cashier and self.current_cashier.role in ("Admin", "Filialleiter"):
            allowed = (
                ROLE_CHOICES
                if self.current_cashier.role == "Admin"
                else ("Kassierer", "Lagerist")
            )
            frame = CashierAdminFrame(
                self.container, self.cr, on_back or self.show_menu, allowed_roles=allowed
            )
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Verwaltung.")

    def open_drawer_admin(self, on_back=None):
        if self.current_cashier and self.current_cashier.role == "Admin":
            frame = DrawerAdminFrame(self.container, self.cr, on_back or self.show_menu)
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Verwaltung.")

    def open_tax_admin(self):
        if self.current_cashier and self.current_cashier.role in ("Admin", "Steuerberater"):
            frame = TaxAdminFrame(self.container, self.cr, on_back=self.show_menu)
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Steuerverwaltung.")

    def open_daily_close(self):
        if self.current_cashier and self.current_cashier.role in (
            "Admin",
            "Kassierer",
            "Filialleiter",
        ):
            if self.cr.day_closed:
                messagebox.showerror("Fehler", "Tagesabschluss bereits durchgeführt.")
                return
            frame = DailyCloseFrame(
                self.container, self.cr, self.settings, on_back=self.show_menu
            )
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Tagesabschluss.")

    def open_cash_management(self):
        if self.current_cashier and self.current_cashier.role in ("Admin", "Filialleiter"):
            frame = CashManagementFrame(
                self.container, self.cr, self.settings, on_back=self.show_menu
            )
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Geldwirtschaft.")


    def open_journal_menu(self):
        frame = JournalMenuFrame(
            self.container,
            open_cash=lambda: self.open_cash_journal(on_back=self.open_journal_menu),
            open_inventory=lambda: self.open_inventory_journal(on_back=self.open_journal_menu),
            open_safe=lambda: self.open_safe_journal(on_back=self.open_journal_menu),
            open_recon=lambda: self.open_reconcile_journal(on_back=self.open_journal_menu),
            open_dayclose=lambda: self.open_daily_close_journal(on_back=self.open_journal_menu),
            on_back=self.show_menu,
        )
        self.switch_frame(frame)

    def open_cash_journal(self, on_back=None):
        frame = CashJournalFrame(
            self.container, self.cr, self.settings, on_back=on_back or self.show_menu
        )
        self.switch_frame(frame)

    def open_inventory_journal(self, on_back=None):
        frame = InventoryJournalFrame(
            self.container, self.cr, on_back=on_back or self.show_menu
        )
        self.switch_frame(frame)

    def open_safe_journal(self, on_back=None):
        frame = SafeJournalFrame(
            self.container, self.cr, self.settings, on_back=on_back or self.show_menu
        )
        self.switch_frame(frame)

    def open_reconcile_journal(self, on_back=None):
        frame = ReconcileJournalFrame(
            self.container, self.cr, self.settings, on_back=on_back or self.show_menu
        )
        self.switch_frame(frame)

    def open_daily_close_journal(self, on_back=None):
        frame = DailyCloseJournalFrame(
            self.container, self.cr, self.settings, on_back=on_back or self.show_menu
        )
        self.switch_frame(frame)

    def open_technik(self):
        if self.current_cashier and self.current_cashier.role in ("Admin", "Techniker"):
            frame = TechnikFrame(self.container, self, on_back=self.show_menu)
            self.switch_frame(frame)
        else:
            messagebox.showerror("Fehler", "Keine Berechtigung für Technik.")

    def switch_frame(self, frame: ttk.Frame):
        if self.current_frame:
            self.current_frame.destroy()
        self.current_frame = frame
        self.current_frame.pack(fill=tk.BOTH, expand=True)


if __name__ == "__main__":
    root = tk.Tk()
    configure_styles(root)
    app = CashRegisterApp(root)
    root.mainloop()

