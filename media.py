import pyautogui
import time


# ==========================================
# PLAY / PAUSE
# ==========================================

def pausar_continuar():

    print("▶️ Play/Pause")

    pyautogui.press(
        "playpause"
    )


def spotify_play_pause():

    print("▶️ Spotify Play/Pause")

    pyautogui.press(
        "playpause"
    )


# ==========================================
# PRÓXIMA MÚSICA
# ==========================================

def proxima_musica():

    print("⏭️ Próxima música")

    pyautogui.press(
        "nexttrack"
    )


def spotify_proxima():

    print("⏭️ Spotify próxima")

    pyautogui.press(
        "nexttrack"
    )


# ==========================================
# MÚSICA ANTERIOR
# ==========================================

def musica_anterior():

    print("⏮️ Música anterior")

    pyautogui.press(
        "prevtrack"
    )


def spotify_anterior():

    print("⏮️ Spotify anterior")

    pyautogui.press(
        "prevtrack"
    )