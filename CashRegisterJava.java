import javax.swing.*;
import javax.swing.border.EmptyBorder;
import javax.swing.table.DefaultTableModel;
import java.awt.*;
import java.io.*;
import java.nio.file.*;
import java.text.DecimalFormat;
import java.time.LocalDateTime;
import java.util.List;
import java.util.*;
import java.util.stream.Collectors;

public class CashRegisterJava {
    public enum Mode { POS, BACKOFFICE }
    public enum Role { ADMIN, KASSIERER, LAGERIST, FILIALLEITER, STEUERBERATER, TECHNIKER }

    static final Path STATE_FILE = Paths.get("java_pos_state.bin");
    static final Path INVENTORY_DB = Paths.get("inventory.db");
    static final Path USERS_DB = Paths.get("users.db");
    static final Path DRAWERS_DB = Paths.get("drawers.db");
    static final DecimalFormat MONEY = new DecimalFormat("0.00");

    static class Product implements Serializable {
        String sku;
        String name;
        double price;
        int stock;
        double taxRate;
        Product(String sku, String name, double price, int stock, double taxRate) {
            this.sku = sku; this.name = name; this.price = price; this.stock = stock; this.taxRate = taxRate;
        }
    }

    static class Cashier implements Serializable {
        String id;
        String pin;
        String name;
        Role role;
        Cashier(String id, String pin, String name, Role role) {
            this.id = id; this.pin = pin; this.name = name; this.role = role;
        }
    }

    static class Drawer implements Serializable {
        String name;
        double balance;
        boolean open;
        String openedBy;
        boolean reconciled = true;
        Drawer(String name) { this.name = name; }
    }

    static class ReceiptItem implements Serializable {
        String sku;
        String name;
        int qty;
        double net;
        double tax;
        double gross;
    }

    static class Receipt implements Serializable {
        String timestamp;
        String cashier;
        List<ReceiptItem> items = new ArrayList<>();
        double net;
        double tax;
        double gross;
    }

    static class Assignment implements Serializable {
        String register;
        String drawer;
        String cashierId;
        String assignedAt;
    }

    static class State implements Serializable {
        Map<String, Product> products = new LinkedHashMap<>();
        Map<String, Cashier> cashiers = new LinkedHashMap<>();
        Map<String, Drawer> drawers = new LinkedHashMap<>();
        Set<String> registers = new LinkedHashSet<>();
        Map<String, Assignment> registerToDrawer = new LinkedHashMap<>();
        List<Receipt> receipts = new ArrayList<>();
        double safeBalance = 1000.0;
    }

    static class Core {
        State state;
        String currentDrawer;
        String currentRegister;

        Core() {
            state = load();
            loadFromLegacyDatabases();
            seedIfNeeded();
            save();
        }

        private State load() {
            if (Files.exists(STATE_FILE)) {
                try (ObjectInputStream in = new ObjectInputStream(Files.newInputStream(STATE_FILE))) {
                    return (State) in.readObject();
                } catch (Exception ignored) {}
            }
            return new State();
        }

        void save() {
            saveToLegacyDatabases();
            try (ObjectOutputStream out = new ObjectOutputStream(Files.newOutputStream(STATE_FILE))) {
                out.writeObject(state);
            } catch (Exception ex) {
                throw new RuntimeException(ex);
            }
        }

        private List<String[]> querySqlite(Path db, String sql) {
            try {
                List<String> cmd = List.of("sqlite3", "-separator", "\t", db.toString(), sql);
                Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
                List<String> lines;
                try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                    lines = br.lines().collect(Collectors.toList());
                }
                p.waitFor();
                List<String[]> out = new ArrayList<>();
                for (String line : lines) {
                    if (line == null || line.isBlank()) continue;
                    out.add(line.split("\\t", -1));
                }
                return out;
            } catch (Exception ignored) {
                return List.of();
            }
        }

        private void execSqlite(Path db, String sql) {
            try {
                new ProcessBuilder("sqlite3", db.toString(), sql)
                        .redirectErrorStream(true)
                        .start()
                        .waitFor();
            } catch (Exception ignored) {}
        }

        private String esc(String v) {
            return v == null ? "" : v.replace("'", "''");
        }

        private void loadFromLegacyDatabases() {
            if (Files.exists(INVENTORY_DB)) {
                for (String[] r : querySqlite(INVENTORY_DB,
                        "SELECT sku,name,price,stock,COALESCE(tax_rate,0) FROM products")) {
                    if (r.length < 5) continue;
                    String sku = r[0];
                    String name = r[1];
                    double price = r[2].isBlank() ? 0.0 : Double.parseDouble(r[2]);
                    int stock = r[3].isBlank() ? 0 : Integer.parseInt(r[3]);
                    double tax = r[4].isBlank() ? 0.0 : Double.parseDouble(r[4]);
                    state.products.put(sku, new Product(sku, name, price, stock, tax));
                }
            }
            if (Files.exists(USERS_DB)) {
                for (String[] r : querySqlite(USERS_DB,
                        "SELECT personnel_number,pin,name,COALESCE(role,'Kassierer') FROM cashiers")) {
                    if (r.length < 4) continue;
                    String id = r[0];
                    String pin = r[1];
                    String name = r[2];
                    Role role;
                    try {
                        role = Role.valueOf(r[3].trim().toUpperCase(Locale.ROOT));
                    } catch (Exception ex) {
                        role = Role.KASSIERER;
                    }
                    state.cashiers.put(id, new Cashier(id, pin, name, role));
                }
            }
            if (Files.exists(DRAWERS_DB)) {
                for (String[] r : querySqlite(DRAWERS_DB,
                        "SELECT name,COALESCE(balance,0),COALESCE(open,0),COALESCE(opened_by,''),COALESCE(reconciled,1) FROM drawers")) {
                    if (r.length < 5) continue;
                    Drawer d = new Drawer(r[0]);
                    d.balance = r[1].isBlank() ? 0.0 : Double.parseDouble(r[1]);
                    d.open = "1".equals(r[2]);
                    d.openedBy = r[3].isBlank() ? null : r[3];
                    d.reconciled = "1".equals(r[4]);
                    state.drawers.put(d.name, d);
                }
                for (String[] r : querySqlite(DRAWERS_DB, "SELECT name FROM registers")) {
                    if (r.length > 0 && !r[0].isBlank()) state.registers.add(r[0]);
                }
                for (String[] r : querySqlite(DRAWERS_DB,
                        "SELECT register_name,drawer_name,cashier_id,assigned_at FROM register_drawer_current")) {
                    if (r.length < 2) continue;
                    Assignment a = new Assignment();
                    a.register = r[0];
                    a.drawer = r[1];
                    a.cashierId = r.length > 2 ? r[2] : "";
                    a.assignedAt = r.length > 3 ? r[3] : "";
                    state.registerToDrawer.put(a.register, a);
                }
                List<String[]> safeRows = querySqlite(DRAWERS_DB, "SELECT balance FROM safe WHERE id=1");
                if (!safeRows.isEmpty() && safeRows.get(0).length > 0 && !safeRows.get(0)[0].isBlank()) {
                    state.safeBalance = Double.parseDouble(safeRows.get(0)[0]);
                }
            }
        }

        private void saveToLegacyDatabases() {
            execSqlite(INVENTORY_DB,
                    "CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY,name TEXT,price REAL,stock INTEGER,min_age INTEGER,tax_rate REAL)");
            execSqlite(USERS_DB,
                    "CREATE TABLE IF NOT EXISTS cashiers(personnel_number TEXT PRIMARY KEY,pin TEXT,name TEXT,role TEXT)");
            execSqlite(DRAWERS_DB,
                    "CREATE TABLE IF NOT EXISTS drawers(name TEXT PRIMARY KEY,balance REAL NOT NULL DEFAULT 0.0,opening_balance REAL NOT NULL DEFAULT 0.0,open INTEGER NOT NULL DEFAULT 0,opened_by TEXT,reconciled INTEGER NOT NULL DEFAULT 1)");
            execSqlite(DRAWERS_DB, "CREATE TABLE IF NOT EXISTS registers(name TEXT PRIMARY KEY)");
            execSqlite(DRAWERS_DB,
                    "CREATE TABLE IF NOT EXISTS register_drawer_current(register_name TEXT PRIMARY KEY,drawer_name TEXT UNIQUE,assigned_at TEXT,cashier_id TEXT)");
            execSqlite(DRAWERS_DB,
                    "CREATE TABLE IF NOT EXISTS safe(id INTEGER PRIMARY KEY CHECK(id=1), balance REAL NOT NULL DEFAULT 0.0)");

            execSqlite(INVENTORY_DB, "DELETE FROM products");
            for (Product p : state.products.values()) {
                execSqlite(INVENTORY_DB,
                        "INSERT INTO products(sku,name,price,stock,min_age,tax_rate) VALUES('" + esc(p.sku) + "','" + esc(p.name) + "'," + p.price + "," + p.stock + ",NULL," + p.taxRate + ")");
            }

            execSqlite(USERS_DB, "DELETE FROM cashiers");
            for (Cashier c : state.cashiers.values()) {
                execSqlite(USERS_DB,
                        "INSERT INTO cashiers(personnel_number,pin,name,role) VALUES('" + esc(c.id) + "','" + esc(c.pin) + "','" + esc(c.name) + "','" + esc(c.role.name()) + "')");
            }

            execSqlite(DRAWERS_DB, "DELETE FROM drawers");
            for (Drawer d : state.drawers.values()) {
                execSqlite(DRAWERS_DB,
                        "INSERT INTO drawers(name,balance,opening_balance,open,opened_by,reconciled) VALUES('" + esc(d.name) + "'," + d.balance + "," + d.balance + "," + (d.open ? 1 : 0) + ",'" + esc(d.openedBy == null ? "" : d.openedBy) + "'," + (d.reconciled ? 1 : 0) + ")");
            }

            execSqlite(DRAWERS_DB, "DELETE FROM registers");
            for (String r : state.registers) execSqlite(DRAWERS_DB, "INSERT INTO registers(name) VALUES('" + esc(r) + "')");

            execSqlite(DRAWERS_DB, "DELETE FROM register_drawer_current");
            for (Assignment a : state.registerToDrawer.values()) {
                execSqlite(DRAWERS_DB,
                        "INSERT INTO register_drawer_current(register_name,drawer_name,assigned_at,cashier_id) VALUES('" + esc(a.register) + "','" + esc(a.drawer) + "','" + esc(a.assignedAt) + "','" + esc(a.cashierId) + "')");
            }
            execSqlite(DRAWERS_DB, "INSERT OR REPLACE INTO safe(id,balance) VALUES(1," + state.safeBalance + ")");
        }

        private void seedIfNeeded() {
            if (state.cashiers.isEmpty()) {
                state.cashiers.put("admin", new Cashier("admin", "admin", "Admin", Role.ADMIN));
                state.cashiers.put("1001", new Cashier("1001", "1234", "Kassierer 1", Role.KASSIERER));
                state.cashiers.put("1002", new Cashier("1002", "1234", "Filialleiter", Role.FILIALLEITER));
            }
            if (state.products.isEmpty()) {
                state.products.put("1", new Product("1", "Wasser", 1.00, 100, 7.0));
                state.products.put("2", new Product("2", "Brot", 2.50, 50, 7.0));
                state.products.put("3", new Product("3", "Kaffee", 3.00, 40, 19.0));
            }
            if (state.drawers.isEmpty()) {
                state.drawers.put("Schublade 1", new Drawer("Schublade 1"));
                state.drawers.put("Schublade 2", new Drawer("Schublade 2"));
                state.drawers.put("Schublade 3", new Drawer("Schublade 3"));
            }
            if (state.registers.isEmpty()) {
                state.registers.add("Kasse 1");
                state.registers.add("Kasse 2");
                state.registers.add("Kasse 3");
            }
            save();
        }

        Cashier login(String id, String pin, Mode mode) {
            Cashier c = state.cashiers.get(id);
            if (c == null || !Objects.equals(c.pin, pin)) return null;
            if (mode == Mode.POS && !(c.role == Role.ADMIN || c.role == Role.KASSIERER || c.role == Role.FILIALLEITER)) return null;
            if (mode == Mode.BACKOFFICE && c.role == Role.KASSIERER) return null;
            restoreSession(c.id);
            return c;
        }

        void restoreSession(String cashierId) {
            currentDrawer = null;
            currentRegister = null;
            for (Drawer d : state.drawers.values()) {
                if (d.open && cashierId.equals(d.openedBy)) {
                    currentDrawer = d.name;
                    currentRegister = registerForDrawer(d.name);
                    return;
                }
            }
        }

        String registerForDrawer(String drawer) {
            for (Assignment a : state.registerToDrawer.values()) if (a.drawer.equals(drawer)) return a.register;
            return null;
        }

        List<String> availableDrawers(String cashierId) {
            List<String> list = new ArrayList<>();
            for (Drawer d : state.drawers.values()) {
                if (!d.open || cashierId.equals(d.openedBy)) list.add(d.name);
            }
            return list;
        }

        void startDay(String cashierId, String register, String drawerName, double opening) {
            Drawer d = state.drawers.get(drawerName);
            if (d == null) throw new IllegalArgumentException("Unbekannte Schublade");
            if (!state.registers.contains(register)) throw new IllegalArgumentException("Unbekannte Kasse");
            Assignment existingReg = state.registerToDrawer.get(register);
            if (existingReg != null && !existingReg.drawer.equals(drawerName)) throw new IllegalArgumentException("Kasse bereits belegt");
            String otherReg = registerForDrawer(drawerName);
            if (otherReg != null && !otherReg.equals(register)) throw new IllegalArgumentException("Schublade in anderer Kasse");
            if (d.open && !cashierId.equals(d.openedBy)) throw new IllegalArgumentException("Schublade wird benutzt");
            if (opening < d.balance) throw new IllegalArgumentException("Anfangsbestand kleiner als vorhandener Bestand");
            double needed = opening - d.balance;
            if (needed > state.safeBalance) throw new IllegalArgumentException("Tresorbestand zu klein");
            state.safeBalance -= needed;
            d.balance = opening;
            d.open = true;
            d.openedBy = cashierId;
            d.reconciled = false;
            Assignment a = new Assignment();
            a.register = register; a.drawer = drawerName; a.cashierId = cashierId; a.assignedAt = LocalDateTime.now().toString();
            state.registerToDrawer.put(register, a);
            currentDrawer = drawerName;
            currentRegister = register;
            save();
        }

        Receipt checkout(String cashierId, Map<String,Integer> cart) {
            if (currentDrawer == null) throw new IllegalStateException("Keine geöffnete Schublade");
            Drawer d = state.drawers.get(currentDrawer);
            if (d == null || !d.open || !cashierId.equals(d.openedBy)) throw new IllegalStateException("Schublade nicht verfügbar");

            Receipt r = new Receipt();
            r.timestamp = LocalDateTime.now().toString();
            Cashier c = state.cashiers.get(cashierId);
            r.cashier = c != null ? (c.name + " (" + c.id + ")") : cashierId;
            for (Map.Entry<String,Integer> e : cart.entrySet()) {
                Product p = state.products.get(e.getKey());
                if (p == null) throw new IllegalArgumentException("Artikel nicht gefunden: " + e.getKey());
                int q = e.getValue();
                if (q <= 0) continue;
                if (p.stock < q) throw new IllegalArgumentException("Nicht genug Bestand: " + p.name);
                p.stock -= q;
                double net = p.price * q;
                double tax = net * (p.taxRate / 100.0);
                double gross = net + tax;
                ReceiptItem it = new ReceiptItem();
                it.sku = p.sku; it.name = p.name; it.qty = q; it.net = net; it.tax = tax; it.gross = gross;
                r.items.add(it);
                r.net += net; r.tax += tax; r.gross += gross;
            }
            d.balance += r.gross;
            state.receipts.add(r);
            save();
            return r;
        }

        void reconcile(String cashierId, double counted, double keepInDrawer) {
            if (currentDrawer == null) throw new IllegalStateException("Keine aktive Schublade");
            Drawer d = state.drawers.get(currentDrawer);
            if (d == null || !cashierId.equals(d.openedBy)) throw new IllegalStateException("Keine Berechtigung");
            if (keepInDrawer > counted || keepInDrawer < 0 || counted < 0) throw new IllegalArgumentException("Ungültige Werte");
            double transfer = counted - keepInDrawer;
            state.safeBalance += transfer;
            d.balance = keepInDrawer;
            d.open = false;
            d.openedBy = null;
            d.reconciled = true;
            if (currentRegister != null) state.registerToDrawer.remove(currentRegister);
            currentDrawer = null;
            currentRegister = null;
            save();
        }

        double dailyGross() { return state.receipts.stream().mapToDouble(r -> r.gross).sum(); }

        Map<String, Double> cashierTurnover() {
            Map<String, Double> m = new LinkedHashMap<>();
            for (Receipt r : state.receipts) m.merge(r.cashier, r.gross, Double::sum);
            return m;
        }

        List<String[]> assignmentRows() {
            List<String[]> rows = new ArrayList<>();
            for (String register : state.registers) {
                Assignment a = state.registerToDrawer.get(register);
                if (a == null) rows.add(new String[]{register, "-", "-", "0.00"});
                else {
                    Drawer d = state.drawers.get(a.drawer);
                    rows.add(new String[]{register, a.drawer, a.cashierId, MONEY.format(d == null ? 0 : d.balance)});
                }
            }
            return rows;
        }
    }

    static class App extends JFrame {
        final Core core = new Core();
        final Mode mode;
        Cashier current;
        final CardLayout card = new CardLayout();
        final JPanel root = new JPanel(card);

        final JComboBox<String> userCombo = new JComboBox<>();
        final JPasswordField pinField = new JPasswordField(12);
        final JLabel status = new JLabel(" ");

        final DefaultTableModel cartModel = new DefaultTableModel(new Object[]{"SKU","Artikel","Menge","Brutto"},0);
        final Map<String,Integer> cart = new LinkedHashMap<>();
        final JLabel cashierInfo = new JLabel(" ");

        final DefaultTableModel prodModel = new DefaultTableModel(new Object[]{"SKU","Name","Preis","Bestand","Steuer"},0);
        final JTable productTable = new JTable(prodModel);

        final JLabel reportDaily = new JLabel();
        final DefaultTableModel repCashierModel = new DefaultTableModel(new Object[]{"Kassierer","Umsatz"},0);
        final DefaultTableModel repAssignModel = new DefaultTableModel(new Object[]{"Kasse","Schublade","Kassierer","Saldo"},0);

        App(Mode mode) {
            super("Kassensystem (Java) - " + mode);
            this.mode = mode;
            setDefaultCloseOperation(EXIT_ON_CLOSE);
            setSize(1000, 700);
            setLocationRelativeTo(null);
            setContentPane(root);
            buildLogin();
            buildMenu();
            buildCashier();
            buildInventory();
            buildReports();
            refreshUsers();
            card.show(root, "login");
        }

        void buildLogin() {
            JPanel p = new JPanel(new GridBagLayout());
            p.setBorder(new EmptyBorder(30,30,30,30));
            GridBagConstraints c = new GridBagConstraints();
            c.insets = new Insets(8,8,8,8); c.anchor = GridBagConstraints.WEST;
            c.gridx=0;c.gridy=0; p.add(new JLabel("Benutzer:"), c);
            c.gridx=1; p.add(userCombo, c);
            c.gridx=0;c.gridy=1; p.add(new JLabel("PIN:"), c);
            c.gridx=1; p.add(pinField, c);
            JButton loginBtn = new JButton("Anmelden");
            c.gridy=2; c.gridx=1; p.add(loginBtn,c);
            loginBtn.addActionListener(e -> doLogin());
            root.add(p, "login");
        }

        void buildMenu() {
            JPanel p = new JPanel(new BorderLayout());
            JPanel b = new JPanel(); b.setLayout(new BoxLayout(b, BoxLayout.Y_AXIS)); b.setBorder(new EmptyBorder(20,20,20,20));
            JButton kasse = new JButton("Kasse");
            JButton war = new JButton("Warenwirtschaft");
            JButton ber = new JButton("Berichte");
            JButton logout = new JButton("Abmelden");
            for (JButton x : List.of(kasse, war, ber, logout)) { x.setAlignmentX(Component.LEFT_ALIGNMENT); b.add(x); b.add(Box.createVerticalStrut(10)); }
            kasse.addActionListener(e -> openCashier());
            war.addActionListener(e -> { refreshProducts(); card.show(root, "inventory"); });
            ber.addActionListener(e -> { refreshReports(); card.show(root, "reports"); });
            logout.addActionListener(e -> logout());
            p.add(new JLabel("Hauptmenü"), BorderLayout.NORTH);
            p.add(b, BorderLayout.WEST);
            p.add(status, BorderLayout.SOUTH);
            root.add(p, "menu");
        }

        void buildCashier() {
            JPanel p = new JPanel(new BorderLayout());
            p.setBorder(new EmptyBorder(8,8,8,8));
            JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
            top.add(cashierInfo);
            JTextField sku = new JTextField(6);
            JTextField qty = new JTextField("1",4);
            JButton add = new JButton("Hinzufügen");
            JButton finish = new JButton("Abschließen");
            JButton reconcile = new JButton("Abrechnen+Abmelden");
            JButton back = new JButton("Zurück");
            top.add(new JLabel("SKU")); top.add(sku); top.add(new JLabel("Menge")); top.add(qty); top.add(add); top.add(finish); top.add(reconcile); top.add(back);
            JTable table = new JTable(cartModel);
            p.add(top, BorderLayout.NORTH);
            p.add(new JScrollPane(table), BorderLayout.CENTER);
            add.addActionListener(e -> {
                String s = sku.getText().trim();
                int q;
                try { q = Integer.parseInt(qty.getText().trim()); } catch(Exception ex){ JOptionPane.showMessageDialog(this,"Menge ungültig"); return; }
                Product pr = core.state.products.get(s);
                if (pr == null) { JOptionPane.showMessageDialog(this,"Artikel nicht gefunden"); return; }
                cart.merge(s, q, Integer::sum);
                redrawCart();
            });
            finish.addActionListener(e -> {
                try {
                    Receipt r = core.checkout(current.id, cart);
                    cart.clear(); redrawCart();
                    JOptionPane.showMessageDialog(this, "Verkauf gespeichert: " + MONEY.format(r.gross));
                } catch (Exception ex) { JOptionPane.showMessageDialog(this, ex.getMessage()); }
            });
            reconcile.addActionListener(e -> reconcileAndLogout());
            back.addActionListener(e -> card.show(root, "menu"));
            root.add(p, "cashier");
        }

        void buildInventory() {
            JPanel p = new JPanel(new BorderLayout());
            JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
            JTextField sku = new JTextField(4), name = new JTextField(10), price = new JTextField(6), stock = new JTextField(5), tax = new JTextField(4);
            JButton add = new JButton("Speichern"), back = new JButton("Zurück");
            top.add(new JLabel("SKU")); top.add(sku); top.add(new JLabel("Name")); top.add(name); top.add(new JLabel("Preis")); top.add(price);
            top.add(new JLabel("Bestand")); top.add(stock); top.add(new JLabel("Steuer")); top.add(tax); top.add(add); top.add(back);
            p.add(top, BorderLayout.NORTH);
            p.add(new JScrollPane(productTable), BorderLayout.CENTER);
            add.addActionListener(e -> {
                try {
                    String s=sku.getText().trim();
                    Product p1 = core.state.products.get(s);
                    if (p1 == null) p1 = new Product(s, name.getText().trim(), Double.parseDouble(price.getText().trim()), Integer.parseInt(stock.getText().trim()), Double.parseDouble(tax.getText().trim()));
                    else { p1.name=name.getText().trim(); p1.price=Double.parseDouble(price.getText().trim()); p1.stock=Integer.parseInt(stock.getText().trim()); p1.taxRate=Double.parseDouble(tax.getText().trim()); }
                    core.state.products.put(s,p1); core.save(); refreshProducts();
                } catch (Exception ex) { JOptionPane.showMessageDialog(this, "Eingabe ungültig"); }
            });
            back.addActionListener(e -> card.show(root, "menu"));
            root.add(p, "inventory");
        }

        void buildReports() {
            JPanel p = new JPanel(new BorderLayout());
            JPanel top = new JPanel(new FlowLayout(FlowLayout.LEFT));
            JButton refresh = new JButton("Aktualisieren"), back = new JButton("Zurück");
            top.add(new JLabel("Berichte")); top.add(reportDaily); top.add(refresh); top.add(back);
            JSplitPane split = new JSplitPane(JSplitPane.HORIZONTAL_SPLIT,
                    new JScrollPane(new JTable(repCashierModel)),
                    new JScrollPane(new JTable(repAssignModel)));
            split.setDividerLocation(450);
            p.add(top, BorderLayout.NORTH);
            p.add(split, BorderLayout.CENTER);
            refresh.addActionListener(e -> refreshReports());
            back.addActionListener(e -> card.show(root, "menu"));
            root.add(p, "reports");
        }

        void refreshUsers() {
            userCombo.removeAllItems();
            for (Cashier c : core.state.cashiers.values()) {
                if (mode == Mode.POS && !(c.role == Role.ADMIN || c.role == Role.KASSIERER || c.role == Role.FILIALLEITER)) continue;
                if (mode == Mode.BACKOFFICE && c.role == Role.KASSIERER) continue;
                userCombo.addItem(c.id + " - " + c.name + " (" + c.role + ")");
            }
        }

        void doLogin() {
            String selected = (String) userCombo.getSelectedItem();
            if (selected == null) return;
            String id = selected.split(" - ")[0].trim();
            Cashier c = core.login(id, new String(pinField.getPassword()), mode);
            if (c == null) { JOptionPane.showMessageDialog(this, "Login fehlgeschlagen"); return; }
            current = c;
            status.setText("Benutzer: " + c.name + " (" + c.role + ") | Tresor: " + MONEY.format(core.state.safeBalance));
            if (c.role == Role.KASSIERER && mode == Mode.POS) openCashier();
            else card.show(root, "menu");
        }

        void openCashier() {
            if (current == null) return;
            core.restoreSession(current.id);
            if (core.currentDrawer == null) {
                List<String> drawers = core.availableDrawers(current.id);
                if (drawers.isEmpty()) { JOptionPane.showMessageDialog(this, "Keine Schublade verfügbar"); return; }
                String drawer = (String) JOptionPane.showInputDialog(this, "Schublade", "Start", JOptionPane.QUESTION_MESSAGE, null, drawers.toArray(), drawers.get(0));
                if (drawer == null) return;
                String register = (String) JOptionPane.showInputDialog(this, "Kasse", "Start", JOptionPane.QUESTION_MESSAGE, null, core.state.registers.toArray(), core.state.registers.iterator().next());
                if (register == null) return;
                String s = JOptionPane.showInputDialog(this, "Anfangsbestand", "0.00");
                if (s == null) return;
                try {
                    core.startDay(current.id, register, drawer, Double.parseDouble(s));
                } catch (Exception ex) { JOptionPane.showMessageDialog(this, ex.getMessage()); return; }
            }
            cashierInfo.setText("Kasse: " + core.currentRegister + " | Schublade: " + core.currentDrawer);
            cart.clear(); redrawCart();
            card.show(root, "cashier");
        }

        void redrawCart() {
            cartModel.setRowCount(0);
            for (var e : cart.entrySet()) {
                Product p = core.state.products.get(e.getKey());
                if (p == null) continue;
                double gross = (p.price * (1 + p.taxRate/100.0)) * e.getValue();
                cartModel.addRow(new Object[]{p.sku, p.name, e.getValue(), MONEY.format(gross)});
            }
        }

        void refreshProducts() {
            prodModel.setRowCount(0);
            for (Product p : core.state.products.values()) prodModel.addRow(new Object[]{p.sku,p.name,MONEY.format(p.price),p.stock,p.taxRate});
        }

        void refreshReports() {
            reportDaily.setText("Tagesumsatz: " + MONEY.format(core.dailyGross()));
            repCashierModel.setRowCount(0);
            for (var e : core.cashierTurnover().entrySet()) repCashierModel.addRow(new Object[]{e.getKey(), MONEY.format(e.getValue())});
            repAssignModel.setRowCount(0);
            for (String[] r : core.assignmentRows()) repAssignModel.addRow(r);
        }

        void reconcileAndLogout() {
            if (current == null || core.currentDrawer == null) { logout(); return; }
            String counted = JOptionPane.showInputDialog(this, "Gezählter Bestand", "0.00");
            if (counted == null) return;
            String keep = JOptionPane.showInputDialog(this, "Betrag in Kasse behalten", "0.00");
            if (keep == null) return;
            try {
                core.reconcile(current.id, Double.parseDouble(counted), Double.parseDouble(keep));
                logout();
            } catch (Exception ex) { JOptionPane.showMessageDialog(this, ex.getMessage()); }
        }

        void logout() {
            current = null;
            pinField.setText("");
            refreshUsers();
            card.show(root, "login");
        }
    }

    public static void main(String[] args) {
        Mode mode = Mode.BACKOFFICE;
        if (args.length > 0 && "pos".equalsIgnoreCase(args[0])) mode = Mode.POS;
        final Mode finalMode = mode;
        SwingUtilities.invokeLater(() -> new App(finalMode).setVisible(true));
    }
}
