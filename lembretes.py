# ==========================================
# JARVIS - SISTEMA DE LEMBRETES
# ==========================================

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

ARQUIVO_LEMBRETES = "lembretes.json"

# Impede que a thread de verificação e o comando de voz
# escrevam no arquivo ao mesmo tempo (isso apagava lembretes novos).
_trava = threading.RLock()


# ==========================================
# CARREGAR / SALVAR
# ==========================================

def carregar_lembretes():

    with _trava:

        if not os.path.exists(ARQUIVO_LEMBRETES):
            return []

        try:
            with open(ARQUIVO_LEMBRETES, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            return dados if isinstance(dados, list) else []

        except Exception as erro:
            print("❌ Erro ao carregar lembretes:", erro)
            return []


def salvar_lembretes(lembretes):

    with _trava:

        try:
            with open(ARQUIVO_LEMBRETES, "w", encoding="utf-8") as arquivo:
                json.dump(lembretes, arquivo, ensure_ascii=False, indent=4)
            return True

        except Exception as erro:
            print("❌ Erro ao salvar lembretes:", erro)
            return False


# ==========================================
# CRIAR / REMOVER / LISTAR
# ==========================================

def criar_lembrete(texto, horario):

    with _trava:

        lembretes = carregar_lembretes()

        novo = {
            "id": int(time.time() * 1000),
            "texto": texto,
            "horario": horario.isoformat(),
            "concluido": False
        }

        lembretes.append(novo)
        salvar_lembretes(lembretes)

    print()
    print("⏰ Lembrete criado!")
    print("📝 Texto:", texto)
    print("📅 Horário:", horario.strftime("%d/%m/%Y %H:%M"))

    return novo


def remover_lembrete(lembrete_id):

    with _trava:

        lembretes = carregar_lembretes()
        novos = [l for l in lembretes if l["id"] != lembrete_id]

        if len(novos) == len(lembretes):
            return False

        salvar_lembretes(novos)
        return True


def listar_lembretes():

    return [
        l for l in carregar_lembretes()
        if not l.get("concluido", False)
    ]


# ==========================================
# INTERPRETAR FRASES
# ==========================================

PEDIDO_LEMBRETE = re.compile(
    r"\b(me lembr(?:e|a|ar)|lembre-me|lembra-me|lembrete)\b"
)

PEDIDO_LISTAR = re.compile(
    r"(meus lembretes|quais (?:são|sao) (?:os )?(?:meus )?lembretes|"
    r"(?:mostrar|mostre|listar|liste|ver) (?:os |meus )?lembretes)"
)

NUMEROS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "quinze": 15, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50
}

_NUM = r"(\d+|" + "|".join(NUMEROS) + r")"

RE_DAQUI = re.compile(
    r"\b(?:daqui\s+(?:a\s+)?|em\s+)"
    + _NUM +
    r"\s*(minutos?|min|horas?)\b"
)

RE_MEIA_HORA = re.compile(r"\b(?:daqui\s+(?:a\s+)?|em\s+)meia\s+hora\b")

RE_HORARIO = re.compile(
    r"\b(?:às|as)\s+(\d{1,2})"
    r"(?:\s*(?::|h)\s*(\d{2})|\s*horas?)?"
    r"(?:\s+e\s+(meia|quinze|quarenta e cinco))?"
    r"(?:\s+da\s+(manhã|manha|tarde|noite|madrugada))?"
)


def _numero(valor):

    valor = valor.strip()

    if valor.isdigit():
        return int(valor)

    return NUMEROS.get(valor)


def extrair_horario(comando, agora=None):
    """
    Procura um horário na frase.
    Retorna (datetime, (inicio, fim)) ou None.
    """

    agora = agora or datetime.now()
    comando = comando.lower()

    # ---- meia hora ----
    achou = RE_MEIA_HORA.search(comando)

    if achou:
        return agora + timedelta(minutes=30), achou.span()

    # ---- daqui a X minutos/horas ----
    achou = RE_DAQUI.search(comando)

    if achou:

        quantidade = _numero(achou.group(1))

        if quantidade is None or quantidade <= 0:
            return None

        if achou.group(2).startswith("h"):
            delta = timedelta(hours=quantidade)
        else:
            delta = timedelta(minutes=quantidade)

        return agora + delta, achou.span()

    # ---- às HH:MM ----
    achou = RE_HORARIO.search(comando)

    if achou:

        hora = int(achou.group(1))
        minuto = int(achou.group(2)) if achou.group(2) else 0

        extra = achou.group(3)

        if extra == "meia":
            minuto = 30
        elif extra == "quinze":
            minuto = 15
        elif extra == "quarenta e cinco":
            minuto = 45

        periodo = achou.group(4)

        if periodo in ("tarde", "noite") and hora < 12:
            hora += 12

        if hora > 23 or minuto > 59:
            return None

        horario = agora.replace(
            hour=hora, minute=minuto, second=0, microsecond=0
        )

        if "amanhã" in comando or "amanha" in comando:
            horario += timedelta(days=1)

        elif horario <= agora:
            horario += timedelta(days=1)

        return horario, achou.span()

    return None


def limpar_texto_lembrete(comando, span=None):

    texto = comando.lower()

    if span:
        texto = texto[:span[0]] + " " + texto[span[1]:]

    texto = PEDIDO_LEMBRETE.sub(" ", texto)

    for palavra in ("jarvis", "amanhã", "amanha", "hoje"):
        texto = re.sub(r"\b" + palavra + r"\b", " ", texto)

    texto = re.sub(r"\s+", " ", texto).strip(" ,.-")

    # tira "de", "que", "para" do começo: "de ligar pra mãe" -> "ligar pra mãe"
    texto = re.sub(r"^(?:de|que|para|pra|disso)\s+", "", texto).strip()

    return texto


def eh_pedido_de_lembrete(comando):

    return bool(PEDIDO_LEMBRETE.search(comando.lower()))


def eh_pedido_de_listar(comando):

    return bool(PEDIDO_LISTAR.search(comando.lower()))


def interpretar_lembrete(comando, agora=None):
    """
    Retorna (texto, horario). Qualquer um pode ser None/"" se faltar.
    """

    resultado = extrair_horario(comando, agora)

    if resultado:
        horario, span = resultado
        return limpar_texto_lembrete(comando, span), horario

    return limpar_texto_lembrete(comando), None


# ==========================================
# VERIFICAR LEMBRETES
# ==========================================

def verificar_lembretes(callback):

    print("⏰ Sistema de lembretes ativado!")

    while True:

        try:

            agora = datetime.now()
            vencidos = []

            # Marca como concluído e SALVA antes de falar.
            # Assim, um lembrete criado enquanto o Jarvis fala
            # não é sobrescrito.
            with _trava:

                lembretes = carregar_lembretes()
                alterado = False

                for lembrete in lembretes:

                    if lembrete.get("concluido", False):
                        continue

                    try:
                        horario = datetime.fromisoformat(lembrete["horario"])
                    except Exception:
                        lembrete["concluido"] = True
                        alterado = True
                        continue

                    if agora >= horario:
                        lembrete["concluido"] = True
                        alterado = True
                        vencidos.append(lembrete["texto"])

                if alterado:
                    salvar_lembretes(lembretes)

            for texto in vencidos:

                print()
                print("🔔 LEMBRETE:", texto)

                try:
                    callback(texto)
                except Exception as erro:
                    print("❌ Erro ao avisar lembrete:", erro)

            time.sleep(1)

        except Exception as erro:

            print("❌ Erro no sistema de lembretes:", erro)
            time.sleep(2)


def iniciar_sistema_lembretes(callback):

    thread = threading.Thread(
        target=verificar_lembretes,
        args=(callback,),
        daemon=True
    )

    thread.start()

    return thread
