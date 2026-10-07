DOMAIN = "dtek_outages"
# Could be one or list of channels separated by coma
# Channels recommended to use: @dtek_ua, @dtek_ocr_test, -1002136796167 (NO Medova)
DEFAULT_CHANNELS = "@dtek_ocr_test,-1002136796167"
SAVE_DIR_NAME = "dtek_graphs"

UA_MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}

# Google API
SA_KEY_PATH = "secrets/decoded-bulwark-480712-g5-8b7397169423.json"
SA_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]