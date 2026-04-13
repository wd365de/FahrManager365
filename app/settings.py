ALLOWED_APPOINTMENT_DURATIONS = [90]
BOOKING_STEP_MINUTES = 15
BOOKING_BUFFER_MINUTES = 10

WEEK_SLOT_DURATION_MINUTES = 90
DEFAULT_BOOKABLE_HOURS_BEFORE = 24
STUDENT_DIRECT_BOOKING_START_LEAD_HOURS = 48
STUDENT_DIRECT_BOOKING_WINDOW_HOURS = 72

PLANNER_SETTING_SHOW_LOCKED_SLOTS = "planner.show_locked_slots"
PLANNER_SETTING_AUTO_REMINDERS = "planner.auto_reminders"
SCHOOL_WHATSAPP_NUMBER = "school.whatsapp_number"
SCHOOL_NAME = "school.name"
SCHOOL_PRIMARY_COLOR = "school.primary_color"
MASTER_DATA_APPOINTMENT_TYPES = "master_data.appointment_types"
MASTER_DATA_CLASSES = "master_data.classes"
MASTER_DATA_PRODUCTS = "master_data.products"
MASTER_DATA_PRODUCT_ASSIGNMENTS = "master_data.product_assignments"
MASTER_DATA_VEHICLES = "master_data.vehicles"
MASTER_DATA_PAYMENT_METHODS = "master_data.payment_methods"
MASTER_DATA_COURSES = "master_data.courses"
MASTER_DATA_ISSUE_TYPES = "master_data.issue_types"
MASTER_DATA_PRICE_LISTS = "master_data.price_lists"
MASTER_DATA_TRAINING_CATEGORIES = "master_data.training_categories"
MASTER_DATA_DEFAULT_APPOINTMENT_TYPE = "master_data.default_appointment_type"
MASTER_DATA_DEFAULT_CLASS = "master_data.default_class"
MASTER_DATA_DEFAULT_PRODUCT = "master_data.default_product"
MASTER_DATA_DEFAULT_VEHICLE = "master_data.default_vehicle"

DEFAULT_PRODUCT_ASSIGNMENTS = """Grundbetrag|Grundbetrag
Teilgrundbetrag - Prüfung nicht bestanden|Teilgrundbetrag - Prüfung nicht bestanden
Lehrmaterial|Lehrmaterial
drive.buzz|Lehrmaterial
drive.buzz|drive.buzz
Übungsfahrt|Übungsstunde
Übungsstunde|Übungsstunde
Übungsstunde B196|Übungsstunde
Beobachtungsfahrt|Beobachtungsfahrt
Überlandfahrt|Überlandfahrt
Autobahnfahrt|Autobahnfahrt
Beleuchtungsfahrt|Nachtfahrt
Nachtfahrt|Nachtfahrt
Testfahrt B197|Testfahrt B197
Fahr-Unterricht GQC/GQD|Fahr-Unterricht GQC/GQD
Unterweisung|Unterweisung
Grundfahraufgabe|Grundfahraufgabe
Simulator Stunde|Simulatorstunde
Prüfungsfahrt|Prüfungsfahrt
Fehlstunde|Fehlstunde
Vorstellung zur theoretische Prüfung|Vorstellung zur theoretische Prüfung
Gebühren Prüforganisation Theorie|Gebühren Prüforganisation Theorie
Vorstellung Praxisprüfung Kl. B|Vorstellung zur praktischen Prüfung
Vorstellung Praxisprüfung Kl. BE|Vorstellung zur praktischen Prüfung
Vorstellung zur praktischen Prüfung|Vorstellung zur praktischen Prüfung
Teilprüfung Fahren|Teilprüfung Fahren
Teilprüfung Abfahrtkontrolle und Handfertigkeiten|Teilprüfung Abfahrtkontrolle und Handfertigkeiten
Teilprüfung Verbinden und Trennen|Teilprüfung Verbinden und Trennen
Gebühren Prüforganisation Praktisch|Gebühren Prüforganisation Praktisch
Gebühren Prüforganisation Teilprüfung Fahren|Gebühren Prüforganisation Teilprüfung Fahren
Gebühren Prüforganisation Teilprüfung Verbinden und Trennen|Gebühren Prüforganisation Teilprüfung Verbinden und Trennen
Gebühren Prüforganisation Teilprüfung Abfahrtkontrolle und Handfertigkeiten|Gebühren Prüforganisation Teilprüfung Abfahrtkontrolle und Handfertigkeiten
Bankgebühr bei fehlgeschlagener LivePay Zahlung automatisch berechnen|Bankgebühr bei fehlgeschlagener LivePay Zahlung automatisch berechnen
Modul|Modul
Servicepauschale|Zusatzangebote
VIP-Service|Zusatzangebote
VIP-Service B96|Zusatzangebote"""

PLANNER_SETTING_DEFINITIONS = {
    SCHOOL_NAME: {
        "default": "Fahrschule",
        "label": "Name der Fahrschule",
        "description": "Wird in der Seitenleiste und im Schülerportal angezeigt.",
    },
    SCHOOL_PRIMARY_COLOR: {
        "default": "#e11d48",
        "label": "Schulfarbe (Hex-Code)",
        "description": "Primärfarbe der Oberfläche, z. B. #e11d48 (Rot), #2563eb (Blau), #16a34a (Grün).",
    },
    SCHOOL_WHATSAPP_NUMBER: {
        "default": "",
        "label": "WhatsApp-Nummer der Fahrschule",
        "description": "Internationale Schreibweise ohne +, z. B. 4915123456789. Wird im Schülerportal als Kontakt-Button angezeigt.",
    },
    PLANNER_SETTING_SHOW_LOCKED_SLOTS: {
        "default": "1",
        "label": "Slots vor Freigabe im Schülerportal anzeigen",
        "description": "Wenn aktiv, sehen Fahrschüler zukünftige Slots bereits vorher und erhalten den Hinweis 'Buchbar ab ...'.",
    },
    PLANNER_SETTING_AUTO_REMINDERS: {
        "default": "0",
        "label": "Automatische Terminerinnerungen aktivieren",
        "description": "Wenn aktiv, wird der Versand von automatischen Erinnerungshinweisen für bevorstehende Termine vorbereitet.",
    },
    MASTER_DATA_APPOINTMENT_TYPES: {
        "default": "Fahrstunde\nTheorie\nPrüfung",
        "label": "Terminarten",
        "description": "Stammdatenquelle für Terminart-Feld (eine Zeile = ein Eintrag).",
    },
    MASTER_DATA_CLASSES: {
        "default": "B\nB197\nBE\nB196",
        "label": "Klassen",
        "description": "Stammdatenquelle für Klassenfeld (eine Zeile = ein Eintrag).",
    },
    MASTER_DATA_PRODUCTS: {
        "default": "Überlandfahrt\nAutobahnfahrt\nNachtfahrt\nTestfahrt B197\nSimulator Stunde\nFehlstunde\nÜbungsfahrt\nBeleuchtungsfahrt",
        "label": "Produkte",
        "description": "Stammdatenquelle für Produktfeld (eine Zeile = ein Eintrag).",
    },
    MASTER_DATA_PRODUCT_ASSIGNMENTS: {
        "default": "",
        "label": "Produkte mit Zuordnung",
        "description": "Produktkatalog im Format Produktname|Zuordnung (eine Zeile = ein Eintrag).",
    },
    MASTER_DATA_VEHICLES: {
        "default": "VW ID 3\nVW Golf 8\nVW T-Roc",
        "label": "Fahrzeuge",
        "description": "Stammdatenquelle für Fahrzeugfeld (eine Zeile = ein Eintrag).",
    },
    MASTER_DATA_PAYMENT_METHODS: {
        "default": "ClassicPay\nLivePay\nFlashPay",
        "label": "Zahlungsarten",
        "description": "Stammdatenquelle für Zahlungsarten am Schüler/Kunden.",
    },
    MASTER_DATA_COURSES: {
        "default": "Standardkurs\nIntensivkurs\nFerienkurs",
        "label": "Kurse",
        "description": "Stammdatenquelle für Kursauswahl am Schüler.",
    },
    MASTER_DATA_ISSUE_TYPES: {
        "default": "Ersterteilung\nErweiterung\nUmschreibung",
        "label": "Erteilungsarten",
        "description": "Stammdatenquelle für Erteilungsart am Schüler.",
    },
    MASTER_DATA_PRICE_LISTS: {
        "default": "Standard\nPremium\nBusiness",
        "label": "Preislisten",
        "description": "Stammdatenquelle für Preisliste am Schüler.",
    },
    MASTER_DATA_TRAINING_CATEGORIES: {
        "default": "SN|10\nÜL|5\nAB|4\nNF|3",
        "label": "Ausbildungskategorien",
        "description": "Format Kategorie|Zielwert (eine Zeile = ein Eintrag). Wird im Slot-Tab Ausbildung angezeigt.",
    },
    MASTER_DATA_DEFAULT_APPOINTMENT_TYPE: {
        "default": "Fahrstunde",
        "label": "Standard Terminart",
        "description": "Standardwert für Terminart im Slot-Detail.",
    },
    MASTER_DATA_DEFAULT_CLASS: {
        "default": "B197",
        "label": "Standard Klasse",
        "description": "Standardwert für Klasse im Slot-Detail.",
    },
    MASTER_DATA_DEFAULT_PRODUCT: {
        "default": "Übungsfahrt",
        "label": "Standard Produkt",
        "description": "Standardwert für Produkt im Slot-Detail.",
    },
    MASTER_DATA_DEFAULT_VEHICLE: {
        "default": "VW ID 3",
        "label": "Standard Fahrzeug",
        "description": "Standardwert für Fahrzeug im Slot-Detail.",
    }
}
