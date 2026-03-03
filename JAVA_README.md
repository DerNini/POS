# Java-Kassensystem

Dieses Projekt enthält jetzt eine vollständige Java-Version des Kassenprogramms:

- `CashRegisterJava.java` – Backend + GUI (Swing) + Persistenz
- `Kasse.java` – Startpunkt für POS-Modus
- `Backoffice.java` – Startpunkt für Backoffice-Modus

## Start

```bash
javac CashRegisterJava.java Kasse.java Backoffice.java
java Kasse
# oder
java Backoffice
```

## Persistenz

Die Java-Anwendung nutzt die bestehenden (alten) SQLite-Dateien mit:

- `inventory.db` (Artikel)
- `users.db` (Benutzer)
- `drawers.db` (Schubladen, Kassen, Zuordnung, Tresor)

Zusätzlich wird weiterhin `java_pos_state.bin` als Java-Snapshot geschrieben.
