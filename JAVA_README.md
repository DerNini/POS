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

Die Daten werden in `java_pos_state.bin` gespeichert (Produkte, Benutzer, Schubladen, Kassen, Zuordnungen, Belege, Tresorbestand).
