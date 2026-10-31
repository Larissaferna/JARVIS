# ============================================================
# JARVIS — MAIN.PY
#
# Roda em segundo plano no PC. Ativa com "Jarvis" ou duas palmas.
# A introdução toca UMA vez, na primeira ativação da vida dele.
# ============================================================

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Quando o Windows abre o Jarvis na inicialização, a pasta atual NÃO é
# a pasta do projeto. Sem isso ele não acharia os modelos de voz,
# memoria.json, lembretes.json, intro.mp3...
os.chdir(BASE_DIR)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# ============================================================
# SAÍDA / LOG
# Com pythonw.exe (sem janela) não existe terminal: tudo vai
# para jarvis.log.
# ============================================================

def _preparar_saida():

    try:

        if sys.stdout is None or sys.stderr is None or "--log" in sys.argv:

            caminho = os.path.join(BASE_DIR, "jarvis.log")

            modo = "a"

            if os.path.exists(caminho) and os.path.getsize(caminho) > 2_000_000:
                modo = "w"

            log = open(caminho, modo, encoding="utf-8", buffering=1)

            sys.stdout = log
            sys.stderr = log

        else:

            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    except Exception:
        pass


_preparar_saida()


import atexit
import collections
import datetime
import json
import re
import socket
import subprocess
import threading
import time
import traceback
import urllib.parse
import webbrowser

try:
    import msvcrt
except ImportError:          # fora do Windows
    msvcrt = None

import pygame
import soundfile as sf
import speech_recognition as sr

from kokoro_onnx import Kokoro

import wake_word
from wake_word import esperar_jarvis, eh_jarvis, retirar_wake_word

from respostas import resposta_personalizada, memoria

from lembretes import (
    criar_lembrete,
    listar_lembretes,
    iniciar_sistema_lembretes,
    eh_pedido_de_lembrete,
    eh_pedido_de_listar,
    interpretar_lembrete,
    extrair_horario
)


# ============================================================
# MÓDULOS OPCIONAIS
# Se algum falhar, o Jarvis continua vivo (só aquele comando
# deixa de funcionar).
# ============================================================

def _indisponivel(nome):

    def funcao(*args, **kwargs):
        print(f"⚠️ {nome} indisponível.")
        return None

    return funcao


try:
    from apps import (
        abrir_aplicativo_por_frase,
        pesquisar_spotify,
        abrir_spotify
    )
except Exception as erro:
    print("⚠️ apps.py com problema:", erro)
    abrir_aplicativo_por_frase = _indisponivel("abrir aplicativo")
    pesquisar_spotify = _indisponivel("pesquisar no Spotify")
    abrir_spotify = _indisponivel("Spotify")

try:
    from media import pausar_continuar, proxima_musica, musica_anterior
except Exception as erro:
    print("⚠️ media.py com problema:", erro)
    pausar_continuar = _indisponivel("controle de mídia")
    proxima_musica = _indisponivel("controle de mídia")
    musica_anterior = _indisponivel("controle de mídia")

try:
    from volume import aumentar_volume, diminuir_volume, mutar, desmutar
except Exception as erro:
    print("⚠️ volume.py com problema:", erro)
    aumentar_volume = _indisponivel("volume")
    diminuir_volume = _indisponivel("volume")
    mutar = _indisponivel("volume")
    desmutar = _indisponivel("volume")

try:
    from visao import analisar_tela
    VISAO_DISPONIVEL = True
except Exception as erro:
    print("⚠️ Sistema de visão não encontrado:", erro)
    VISAO_DISPONIVEL = False

try:
    from web_server import iniciar_servidor
    WEB_SERVER_DISPONIVEL = True
except Exception as erro:
    print("⚠️ web_server.py não encontrado:", erro)
    WEB_SERVER_DISPONIVEL = False


# ============================================================
# CONFIGURAÇÕES
# ============================================================

ARQUIVO_KOKORO = "kokoro-v1.0.fp16.onnx"
ARQUIVO_VOZES = "voices-v1.0.bin"

ARQUIVO_VOZ = "jarvis_voz.wav"
ARQUIVO_INTRO = "intro.mp3"
ARQUIVO_ESTADO = "estado_jarvis.json"
ARQUIVO_PID = "jarvis.pid"

VOZ_KOKORO = "pf_dora"
VELOCIDADE_VOZ = 1.0

DURACAO_MAXIMA_INTRO = 17

PORTA_WEB = 5000
ABRIR_INTERFACE_AO_INICIAR = True   # abre a janela do HUD assim que o Jarvis liga
PORTA_TRAVA = 50997          # impede abrir dois Jarvis ao mesmo tempo

SILENCIOS_PARA_DORMIR = 2    # 2 x 10s sem falar -> volta para a espera


# ============================================================
# ESTADO GLOBAL
# ============================================================

kokoro = None
microfone = None

reconhecedor = sr.Recognizer()
reconhecedor.energy_threshold = 200
reconhecedor.dynamic_energy_threshold = True
reconhecedor.pause_threshold = 1.0
reconhecedor.phrase_threshold = 0.2
reconhecedor.non_speaking_duration = 0.5

_lock_fala = threading.RLock()     # só uma fala por vez
_lock_cmd = threading.RLock()      # só um comando por vez (voz ou web)

falas_recentes = collections.deque(maxlen=50)

_modo = "INICIANDO"
_lembrete_pendente = None          # None = nada; "" = esperando o texto
_trava_socket = None


def _definir_modo(novo):

    global _modo
    _modo = novo


# ============================================================
# ESTADO PERSISTENTE (intro só na primeira vez)
# ============================================================

def carregar_estado():

    try:

        if os.path.exists(ARQUIVO_ESTADO):

            with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)

            if isinstance(dados, dict):
                return dados

    except Exception as erro:
        print("⚠️ Erro ao ler estado:", erro)

    return {}


def salvar_estado(estado):

    try:

        with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as arquivo:
            json.dump(estado, arquivo, ensure_ascii=False, indent=4)

    except Exception as erro:
        print("⚠️ Erro ao salvar estado:", erro)


# ============================================================
# VOZ
# ============================================================

def iniciar_voz():

    global kokoro

    print("🔊 Carregando voz do JARVIS...")

    for arquivo in (ARQUIVO_KOKORO, ARQUIVO_VOZES):

        if not os.path.exists(arquivo):
            print(f"❌ Não encontrei: {arquivo}")
            raise SystemExit(1)

    try:

        kokoro = Kokoro(ARQUIVO_KOKORO, ARQUIVO_VOZES)

        if VOZ_KOKORO not in kokoro.get_voices():

            print(f"❌ A voz {VOZ_KOKORO} não foi encontrada.")
            print("Vozes disponíveis:", kokoro.get_voices())
            raise SystemExit(1)

        print("✅ Voz Kokoro carregada!")

    except SystemExit:
        raise

    except Exception as erro:
        print("❌ Erro ao carregar Kokoro:", erro)
        raise SystemExit(1)


def iniciar_audio():

    try:

        pygame.init()

        pygame.mixer.init(
            frequency=24000,
            size=-16,
            channels=2,
            buffer=512
        )

    except Exception as erro:
        print("❌ Erro ao iniciar áudio:", erro)


def falar(texto):

    if texto is None:
        return

    texto = str(texto).strip()

    if not texto:
        return

    print("🤖 JARVIS:", texto)

    falas_recentes.append(texto)

    if kokoro is None:
        return

    with _lock_fala:

        # Avisa o microfone para não ouvir a própria voz
        wake_word.silenciar.set()

        try:

            samples, taxa = kokoro.create(
                texto,
                voice=VOZ_KOKORO,
                speed=VELOCIDADE_VOZ,
                lang="pt-br"
            )

            sf.write(ARQUIVO_VOZ, samples, taxa, subtype="PCM_16")

            if not pygame.mixer.get_init():
                pygame.mixer.init(
                    frequency=taxa, size=-16, channels=1, buffer=512
                )

            pygame.mixer.music.stop()
            time.sleep(0.05)

            pygame.mixer.music.load(ARQUIVO_VOZ)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(30)

            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

        except Exception as erro:
            print("❌ Erro na voz:", erro)

        finally:
            time.sleep(0.25)          # deixa o eco acabar
            wake_word.silenciar.clear()


# ============================================================
# OUVIR (modo conversa)
# ============================================================

def ouvir():

    print()
    print("🎙️ Estou ouvindo seu comando...")

    try:

        with microfone as fonte:

            audio = reconhecedor.listen(
                fonte,
                timeout=10,
                phrase_time_limit=8
            )

    except sr.WaitTimeoutError:
        print("⏳ Nenhuma fala detectada.")
        return ""

    except Exception as erro:
        print("❌ ERRO AO ESCUTAR:", erro)
        return ""

    try:

        texto = reconhecedor.recognize_google(
            audio,
            language="pt-BR"
        ).lower().strip()

        print("🗣️ Você:", texto)

        # "Jarvis, abre o spotify" no meio da conversa também funciona
        if eh_jarvis(texto):
            texto = retirar_wake_word(texto)

        return texto

    except sr.UnknownValueError:
        print("❌ O Google não conseguiu entender o áudio.")
        return ""

    except sr.RequestError as erro:
        print("❌ ERRO NO SERVIÇO DE RECONHECIMENTO:", erro)
        return ""

    except Exception as erro:
        print("❌ ERRO AO RECONHECER:", erro)
        return ""


# ============================================================
# INTRODUÇÃO (só na primeira ativação)
# ============================================================

def tocar_intro():

    if not os.path.exists(ARQUIVO_INTRO):
        print("⚠️ intro.mp3 não encontrado.")
        return

    with _lock_fala:

        wake_word.silenciar.set()

        try:

            print()
            print("🎵 Tocando música de inicialização...")

            pygame.mixer.music.stop()
            pygame.mixer.music.load(ARQUIVO_INTRO)
            pygame.mixer.music.play()

            inicio = time.time()

            while pygame.mixer.music.get_busy():

                # ENTER pula (só funciona se houver terminal aberto)
                if msvcrt is not None:
                    try:
                        if msvcrt.kbhit() and msvcrt.getch() == b"\r":
                            print("⏭️ Introdução pulada!")
                            break
                    except Exception:
                        pass

                if time.time() - inicio >= DURACAO_MAXIMA_INTRO:
                    break

                pygame.time.Clock().tick(30)

            pygame.mixer.music.stop()

            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

        except Exception as erro:
            print("❌ Erro ao tocar a música:", erro)

        finally:
            wake_word.silenciar.clear()


def primeira_ativacao():
    """
    Toca a introdução se for a primeira vez que o Jarvis é ativado.
    Para ver a introdução de novo: apague o arquivo estado_jarvis.json
    """

    estado = carregar_estado()

    if estado.get("intro_tocada"):
        return False

    _definir_modo("INTRODUÇÃO")

    if WEB_SERVER_DISPONIVEL and not ABRIR_INTERFACE_AO_INICIAR:
        abrir_interface(falando=False)

    tocar_intro()

    estado["intro_tocada"] = True
    estado["primeira_ativacao"] = datetime.datetime.now().isoformat()
    salvar_estado(estado)

    nome = memoria.get("nome")

    if nome:
        falar(f"Olá, {nome}. Todos os sistemas estão online. Estou ouvindo.")
    else:
        falar("Olá. Todos os sistemas estão online. Estou ouvindo.")

    return True


# ============================================================
# AUXILIARES DE TEXTO
# ============================================================

def tem(comando, *frases):

    return any(frase in comando for frase in frases)


def palavra(comando, *palavras):
    """Palavra inteira (evita 'ola' dentro de 'escola')."""

    return any(
        re.search(r"\b" + re.escape(p) + r"\b", comando)
        for p in palavras
    )


def iniciar_programa(comando, shell=False):

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return subprocess.Popen(comando, shell=shell, creationflags=flags)


# ============================================================
# INTERFACE (janela de app, sem barra de endereço)
# ============================================================

CAMINHOS_NAVEGADOR = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

FRASES_INTERFACE = (
    "abrir interface", "abrir a interface", "abre a interface",
    "abrir painel", "abrir o painel", "abre o painel",
    "abrir hud", "abrir o hud", "mostrar interface", "mostrar painel",
    "abrir o jarvis", "abre o jarvis", "abrir jarvis"
)


def abrir_interface(falando=True):

    url = f"http://127.0.0.1:{PORTA_WEB}"

    if falando:
        falar("Abrindo a interface.")

    for caminho in CAMINHOS_NAVEGADOR:

        if os.path.exists(caminho):

            try:

                subprocess.Popen([
                    caminho,
                    f"--app={url}",
                    "--window-size=1280,820"
                ])

                return True

            except Exception as erro:
                print("⚠️ Erro ao abrir janela:", erro)

    webbrowser.open(url)

    return False


# ============================================================
# VISÃO
# ============================================================

FRASES_VISAO = (
    "o que está na minha tela", "o que esta na minha tela",
    "o que está na tela", "o que esta na tela",
    "o que tem na minha tela", "o que tem na tela",
    "descreva minha tela", "descreve minha tela",
    "descreva a minha tela", "descreve a minha tela",
    "descreva a tela", "descreve a tela",
    "olhe minha tela", "olha minha tela",
    "olhe a minha tela", "olha a minha tela",
    "veja minha tela", "ve minha tela",
    "veja a minha tela", "ve a minha tela",
    "o que você está vendo", "o que voce esta vendo",
    "o que você vê na minha tela", "o que voce ve na minha tela",
    "o que você vê na tela", "o que voce ve na tela",
    "leia minha tela", "leia a minha tela",
    "ler minha tela", "ler a minha tela",
    "analise minha tela", "analisa minha tela",
    "analise a minha tela", "analisa a minha tela",
    "analise a tela", "analisa a tela",
    "veja o que está na minha tela", "veja o que esta na minha tela"
)


def comando_visao(comando):

    return tem(comando, *FRASES_VISAO)


def olhar_tela():

    if not VISAO_DISPONIVEL:
        falar("Meu sistema de visão não está disponível.")
        return True

    falar("Um momento. Vou analisar sua tela.")

    try:

        resultado = analisar_tela()

        resultado = str(resultado).strip() if resultado else ""

        if not resultado:
            falar("Não consegui identificar o que está na tela.")
            return True

        falar(resultado)

    except Exception as erro:

        print("❌ Erro no sistema de visão:", erro)
        falar("Ocorreu um erro enquanto eu analisava sua tela.")

    return True


# ============================================================
# LEMBRETES
# ============================================================

def avisar_lembrete(texto):

    print()
    print("🔔 LEMBRETE DO JARVIS:", texto)

    falar(f"Lembrete. {texto}")


def _quando_falado(horario):

    agora = datetime.datetime.now()

    if horario.minute:
        hora = f"{horario.hour} horas e {horario.minute} minutos"
    else:
        hora = f"{horario.hour} horas"

    if horario.date() == agora.date():
        return f"às {hora}"

    if horario.date() == (agora + datetime.timedelta(days=1)).date():
        return f"amanhã às {hora}"

    return f"no dia {horario.day} às {hora}"


def _agendar(texto, horario):

    criar_lembrete(texto, horario)

    falar(f"Entendido. Vou te lembrar de {texto} {_quando_falado(horario)}.")

    return True


def mostrar_lembretes():

    lembretes = listar_lembretes()

    if not lembretes:
        falar("Você não tem nenhum lembrete.")
        return

    print()
    print("⏰ SEUS LEMBRETES")

    for numero, lembrete in enumerate(lembretes, start=1):

        horario = datetime.datetime.fromisoformat(lembrete["horario"])

        print(f"{numero}. {lembrete['texto']} - {horario.strftime('%d/%m/%Y %H:%M')}")

    if len(lembretes) == 1:
        falar("Você tem um lembrete ativo.")
    else:
        falar(f"Você tem {len(lembretes)} lembretes ativos.")

    for lembrete in lembretes[:3]:

        horario = datetime.datetime.fromisoformat(lembrete["horario"])

        falar(f"{lembrete['texto']}, {_quando_falado(horario)}.")


def tratar_lembretes(comando):
    """
    Retorna True se tratou o comando, None se não era sobre lembretes.
    """

    global _lembrete_pendente

    # ---- estou esperando "quando?" (ou "o quê?") ----
    if _lembrete_pendente is not None:

        anterior = _lembrete_pendente
        _lembrete_pendente = None

        if tem(comando, "cancelar", "deixa pra lá", "deixa pra la", "esquece", "não precisa", "nao precisa"):
            falar("Tudo bem, cancelei.")
            return True

        texto_novo, horario = interpretar_lembrete(comando)

        texto = anterior or texto_novo

        if texto and horario:
            return _agendar(texto, horario)

        if texto and not anterior:
            _lembrete_pendente = texto
            falar("Para quando? Diga, por exemplo, daqui a dez minutos, ou às 15 horas.")
            return True

        return None          # não era resposta: trata como comando normal

    if eh_pedido_de_listar(comando):
        mostrar_lembretes()
        return True

    if eh_pedido_de_lembrete(comando):

        texto, horario = interpretar_lembrete(comando)

        if horario is None:

            if texto:
                _lembrete_pendente = texto
                falar("Para quando? Diga, por exemplo, daqui a dez minutos, ou às 15 horas.")
            else:
                _lembrete_pendente = ""
                falar("Claro. O que você quer que eu lembre, e quando?")

            return True

        return _agendar(texto or "seu lembrete", horario)

    return None


# ============================================================
# SITES / PESQUISAS
# ============================================================

def abrir_site(nome, url):

    falar(f"Abrindo {nome}.")
    webbrowser.open(url)


def abrir_discord():

    falar("Abrindo o Discord.")

    try:
        os.startfile("discord://")
    except Exception:
        webbrowser.open("https://discord.com/app")


def abrir_vscode():

    falar("Abrindo o Visual Studio Code.")

    try:
        iniciar_programa("code", shell=True)
    except Exception as erro:
        print("❌ Erro ao abrir VS Code:", erro)
        falar("Não consegui abrir o Visual Studio Code.")


def _limpar_pesquisa(comando, extras=()):

    palavras = (
        "jarvis", "pesquise", "pesquisar", "pesquisa",
        "procure", "procurar", "busque", "buscar"
    ) + tuple(extras)

    padrao = r"\b(" + "|".join(re.escape(p) for p in palavras) + r")\b"

    return re.sub(r"\s+", " ", re.sub(padrao, " ", comando)).strip()


def pesquisar(comando):

    consulta = _limpar_pesquisa(
        comando,
        extras=("no google", "na internet", "no youtube", "no spotify")
    )

    if not consulta:
        falar("O que você gostaria que eu pesquisasse?")
        return

    if "youtube" in comando:

        falar(f"Pesquisando {consulta} no YouTube.")

        webbrowser.open(
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(consulta)
        )

        return

    falar(f"Pesquisando por {consulta}.")

    webbrowser.open(
        "https://www.google.com/search?q="
        + urllib.parse.quote_plus(consulta)
    )


def tirar_print():

    falar("Capturando a tela.")

    try:

        from PIL import ImageGrab

        pasta = os.path.join(os.path.expanduser("~"), "Pictures", "Jarvis")
        os.makedirs(pasta, exist_ok=True)

        nome = datetime.datetime.now().strftime("print_%Y-%m-%d_%H-%M-%S.png")
        caminho = os.path.join(pasta, nome)

        ImageGrab.grab(all_screens=True).save(caminho)

        print("📸 Print salvo em:", caminho)

        falar("Print salvo com sucesso.")

    except Exception as erro:

        print("❌ Erro ao tirar o print:", erro)
        falar("Não consegui tirar o print.")


# ============================================================
# COMANDOS DE CONTROLE
# ============================================================

FRASES_DESLIGAR = (
    "desligar jarvis", "desliga jarvis", "desligue jarvis",
    "encerrar jarvis", "encerra jarvis", "parar jarvis", "pare jarvis"
)

DESLIGAR_SOZINHO = {"desligar", "desliga", "desligue", "encerrar", "encerra"}

FRASES_DORMIR = (
    "vai dormir", "vá dormir", "volte a dormir", "volta a dormir",
    "pare de ouvir", "para de ouvir", "modo de espera",
    "entrar em espera", "fique em espera", "pode dormir"
)

FRASES_PAUSA = (
    "pausar música", "pausar musica", "pausar a música", "pausar a musica",
    "pausa a música", "pausa a musica",
    "continuar música", "continuar musica",
    "continua a música", "continua a musica", "continuar a música",
    "continuar a musica"
)

PAUSA_SOZINHA = {
    "play", "pause", "pausa", "pausar", "continuar", "continua",
    "dar play", "da play", "dá play", "dar pause", "dar um play"
}


def comando_desligar(comando):

    # "desligar" sozinho ou "desligar jarvis".
    # NÃO dispara em "me lembra de desligar o forno".
    return comando in DESLIGAR_SOZINHO or tem(comando, *FRASES_DESLIGAR)


def comando_dormir(comando):

    return comando == "dormir" or tem(comando, *FRASES_DORMIR)


# ============================================================
# EXECUTAR COMANDO
# Retorna: True (continua), False (desligar) ou "DORMIR"
# ============================================================

def executar_comando(comando):

    comando = str(comando).lower().strip()

    print()
    print("🧠 Comando processado:", repr(comando))

    # ---------------- LEMBRETES (antes de tudo) ----------------
    # "me lembra de desligar o forno" não pode desligar o Jarvis,
    # nem cair na resposta da Larissa.
    resultado = tratar_lembretes(comando)

    if resultado:
        return True

    # ---------------- DESLIGAR / DORMIR ----------------
    if comando_desligar(comando):
        falar("Desligando meus sistemas. Até logo.")
        return False

    if comando_dormir(comando):
        falar("Entrando em modo de espera.")
        return "DORMIR"

    # ---------------- INTERFACE ----------------
    if tem(comando, *FRASES_INTERFACE):
        abrir_interface()
        return True

    # ---------------- VISÃO ----------------
    if comando_visao(comando):
        return olhar_tela()

    # ---------------- RESPOSTAS.PY (memória, piadas...) ----------------
    try:

        resposta = resposta_personalizada(comando)

        if resposta:
            falar(resposta)
            return True

    except Exception as erro:
        print("⚠️ Erro no respostas.py:", erro)
        traceback.print_exc()

    # ---------------- SAUDAÇÃO / NOME / HORA ----------------
    if palavra(comando, "olá", "ola", "oi") or tem(comando, "bom dia", "boa tarde", "boa noite"):
        falar("Olá. Estou pronto para ajudar.")
        return True

    if tem(comando, "seu nome", "quem é você", "quem e voce"):
        falar("Meu nome é Jarvis.")
        return True

    if tem(comando, "que horas", "horas são", "horas sao"):
        falar(f"Agora são {datetime.datetime.now().strftime('%H:%M')}.")
        return True

    # ---------------- VOLUME ----------------
    if tem(comando, "aumentar volume", "aumenta o volume", "aumente o volume"):
        aumentar_volume()
        falar("Aumentando o volume.")
        return True

    if tem(
        comando, "diminuir volume", "diminui o volume", "diminua o volume",
        "abaixar volume", "abaixa o volume"
    ):
        diminuir_volume()
        falar("Diminuindo o volume.")
        return True

    if tem(comando, "desmutar", "tirar do mudo", "ativar som", "voltar o som"):
        desmutar()
        falar("Som ativado.")
        return True

    if palavra(comando, "mutar", "muta", "silenciar", "silencie"):
        mutar()
        falar("Som mutado.")
        return True

    # ---------------- PROGRAMAS DO WINDOWS ----------------
    if tem(comando, "bloco de notas", "notepad"):
        falar("Abrindo o bloco de notas.")
        iniciar_programa("notepad.exe")
        return True

    if "calculadora" in comando:
        falar("Abrindo a calculadora.")
        iniciar_programa("calc.exe")
        return True

    if tem(comando, "explorador de arquivos") or comando == "explorador":
        falar("Abrindo o explorador de arquivos.")
        iniciar_programa("explorer.exe")
        return True

    if tem(comando, "tirar print", "tirar uma print", "capturar tela", "print da tela"):
        tirar_print()
        return True

    if tem(comando, "abrir downloads", "abrir download") or comando == "downloads":
        falar("Abrindo a pasta de downloads.")
        os.startfile(os.path.join(os.path.expanduser("~"), "Downloads"))
        return True

    if tem(comando, "abrir configurações", "abrir configuracoes", "configurações", "configuracoes"):
        falar("Abrindo as configurações.")
        os.startfile("ms-settings:")
        return True

    # ---------------- MÚSICA ----------------
    if (
        comando in PAUSA_SOZINHA
        or tem(comando, *FRASES_PAUSA)
    ):
        pausar_continuar()
        falar("Comando de reprodução executado.")
        return True

    if comando.startswith((
        "tocar ", "toque ", "toca ", "coloca ", "coloque ", "play "
    )):

        musica = pesquisar_spotify(comando)

        if musica:
            falar(f"Pesquisando {musica} no Spotify.")
        else:
            falar("Qual música você quer ouvir?")

        return True

    if tem(
        comando, "pesquisar no spotify", "pesquise no spotify",
        "pesquisar spotify", "pesquise spotify"
    ):

        resultado = pesquisar_spotify(comando)

        if resultado:
            falar(f"Pesquisando {resultado} no Spotify.")
        else:
            falar("Não consegui fazer a pesquisa.")

        return True

    if tem(
        comando, "próxima música", "proxima musica", "pular música",
        "pular musica"
    ) or comando in ("próxima", "proxima"):
        proxima_musica()
        falar("Pulando para a próxima música.")
        return True

    if tem(
        comando, "música anterior", "musica anterior", "voltar música",
        "voltar musica", "música passada", "musica passada",
        "volta musica", "volta a música", "volta a musica"
    ):
        musica_anterior()
        falar("Voltando para a música anterior.")
        return True

    # ---------------- PESQUISAS (antes de abrir sites) ----------------
    # "pesquise gatos no youtube" precisa pesquisar, não só abrir o site.
    if palavra(
        comando, "pesquise", "pesquisar", "pesquisa",
        "procure", "procurar", "busque", "buscar"
    ):
        pesquisar(comando)
        return True

    # ---------------- SITES E APPS ----------------
    if "discord" in comando:
        abrir_discord()
        return True

    if "youtube" in comando:
        abrir_site("o YouTube", "https://www.youtube.com")
        return True

    if "instagram" in comando:
        abrir_site("o Instagram", "https://www.instagram.com")
        return True

    if comando == "spotify" or tem(comando, "abrir spotify", "abre o spotify", "abra o spotify"):
        abrir_spotify()
        return True

    if "whatsapp" in comando:
        abrir_site("o WhatsApp", "https://web.whatsapp.com")
        return True

    if "gmail" in comando:
        abrir_site("o Gmail", "https://mail.google.com")
        return True

    if "google" in comando:
        abrir_site("o Google", "https://www.google.com")
        return True

    if tem(comando, "vs code", "vscode", "visual studio code"):
        abrir_vscode()
        return True

    # ---------------- ABRIR QUALQUER APLICATIVO ----------------
    if tem(comando, "abrir ", "abre ", "abra ", "inicia ", "iniciar "):

        aplicativo = abrir_aplicativo_por_frase(comando)

        if aplicativo:
            falar(f"Abrindo {aplicativo}.")
        else:
            falar("Não encontrei esse aplicativo.")

        return True

    # ---------------- DESCONHECIDO ----------------
    falar("Ainda não conheço esse comando.")

    return True


def executar_comando_seguro(comando):
    """Um erro num comando nunca derruba o Jarvis."""

    with _lock_cmd:

        try:
            return executar_comando(comando)

        except Exception as erro:

            print("❌ Erro ao executar comando:", erro)
            traceback.print_exc()

            falar("Ocorreu um erro ao executar esse comando.")

            return True


def executar_comando_web(comando):
    """Usado pela interface web: devolve também o que o Jarvis falou."""

    with _lock_cmd:

        falas_recentes.clear()

        resultado = executar_comando_seguro(comando)

        falas = list(falas_recentes)

    if resultado is False:
        # Desligar pela web: encerra o processo depois de responder
        threading.Timer(2.0, os._exit, args=(0,)).start()

    return {"resultado": resultado, "falas": falas}


def obter_estado():

    return {
        "modo": _modo,
        "lembretes": len(listar_lembretes()),
        "visao": VISAO_DISPONIVEL,
        "voz": kokoro is not None,
        "nome": memoria.get("nome")
    }


# ============================================================
# INSTÂNCIA ÚNICA
# ============================================================

def instancia_unica():

    global _trava_socket

    try:

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", PORTA_TRAVA))

        _trava_socket = s

        return True

    except OSError:
        return False


def registrar_pid():

    try:

        with open(ARQUIVO_PID, "w") as arquivo:
            arquivo.write(str(os.getpid()))

        atexit.register(
            lambda: os.path.exists(ARQUIVO_PID) and os.remove(ARQUIVO_PID)
        )

    except Exception:
        pass


# ============================================================
# LOOP PRINCIPAL
# ============================================================

def loop_principal():

    modo_conversa = False
    silencios = 0

    while True:

        try:

            # ================= MODO ESPERA =================
            if not modo_conversa:

                _definir_modo("ESPERA")

                comando_direto = (esperar_jarvis() or "").strip().lower()

                _definir_modo("ATIVO")

                print()
                print("🔵 JARVIS ATIVADO!")

                # Introdução: só na primeira ativação da vida dele
                primeira = primeira_ativacao()

                silencios = 0

                if comando_direto:

                    print("🧠 Comando direto:", repr(comando_direto))

                    resultado = executar_comando_seguro(comando_direto)

                    if resultado is False:
                        return

                    modo_conversa = resultado != "DORMIR"

                    continue

                if not primeira:
                    falar("Sim?")

                modo_conversa = True

                continue

            # ================= MODO CONVERSA =================
            _definir_modo("CONVERSA")

            comando = ouvir()

            if not comando:

                silencios += 1

                if silencios >= SILENCIOS_PARA_DORMIR:

                    print("😴 Sem comandos. Voltando para o modo de espera.")

                    modo_conversa = False

                continue

            silencios = 0

            resultado = executar_comando_seguro(comando)

            if resultado is False:
                return

            if resultado == "DORMIR":
                modo_conversa = False
                print("😴 JARVIS voltou para o modo de espera.")

        except KeyboardInterrupt:

            print("🛑 JARVIS interrompido pelo teclado.")
            return

        except Exception as erro:

            print("❌ Erro no loop principal:", erro)
            traceback.print_exc()

            time.sleep(1)


# ============================================================
# INÍCIO
# ============================================================

def main():

    global microfone

    if not instancia_unica():
        print("⚠️ O JARVIS já está rodando.")
        return

    registrar_pid()

    print()
    print("================================")
    print("          JARVIS ONLINE")
    print("================================")

    iniciar_voz()
    iniciar_audio()

    try:
        microfone = sr.Microphone()
    except Exception as erro:
        print("❌ Erro ao configurar microfone:", erro)
        raise SystemExit(1)

    if WEB_SERVER_DISPONIVEL:

        try:

            iniciar_servidor(
                executar_comando_web,
                host="127.0.0.1",
                port=PORTA_WEB,
                obter_estado=obter_estado
            )

        except Exception as erro:
            print("❌ Erro ao iniciar interface web:", erro)

        if ABRIR_INTERFACE_AO_INICIAR:

            threading.Timer(
                2.5,
                abrir_interface,
                kwargs={"falando": False}
            ).start()

    try:
        iniciar_sistema_lembretes(avisar_lembrete)
    except Exception as erro:
        print("⚠️ Erro ao iniciar lembretes:", erro)

    loop_principal()

    # ---------------- DESLIGANDO ----------------
    for acao in (pygame.mixer.music.stop, pygame.quit):
        try:
            acao()
        except Exception:
            pass

    print()
    print("================================")
    print("        JARVIS OFFLINE")
    print("================================")


if __name__ == "__main__":
    main()
