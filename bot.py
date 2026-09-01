# -*- coding: utf-8 -*-
"""
FOX BREAKING - monitora canais do YouTube e avisa no Telegram.
Le o RSS oficial do YouTube (nao gasta cota de API).
"""

import os
import re
import json
import time
import html
from datetime import datetime, timezone, timedelta

import requests
import feedparser

# =====================================================================
# CONFIGURACAO - so mexa aqui
# =====================================================================

CANAIS = {
    "UCJg9wBPyKMNA5sRDnvzmkdg": "LiveNOW from FOX",
    "UCXIJgqnII2ZOINSWNOGFThA": "Fox News",
}

# Idade maxima do video para ser enviado (em horas).
# Evita despejar coisa velha se o bot ficar parado.
IDADE_MAXIMA_HORAS = 3

# Se True, so envia videos cujo titulo tem um marcador de urgencia.
# Se False, envia TUDO que os canais publicarem.
SO_BREAKING = True

# Marcadores fortes - o que a Fox usa quando e noticia quente de verdade.
MARCADORES = [
    "breaking",
    "just in",
    "urgent",
    "alert",
    "developing",
    "happening now",
    "moments ago",
]

# Marcadores fracos - trazem mais volume, incluindo coisa comum.
# Para ligar, mude USAR_FRACOS para True.
USAR_FRACOS = False

MARCADORES_FRACOS = [
    "watch",
    "live",
    "exclusive",
    "full speech",
    "press conference",
]

# =====================================================================
# NAO PRECISA MEXER DAQUI PARA BAIXO
# =====================================================================

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ARQUIVO_VISTO = "visto.json"
LIMITE_MEMORIA = 400


def carregar_vistos():
    try:
        with open(ARQUIVO_VISTO, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados.get("ids", [])
    except Exception:
        return []


def salvar_vistos(ids):
    with open(ARQUIVO_VISTO, "w", encoding="utf-8") as f:
        json.dump({"ids": ids[-LIMITE_MEMORIA:]}, f, ensure_ascii=False, indent=1)


def buscar_canal(channel_id, nome):
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }

    for tentativa in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                break
            print("  [%s] HTTP %s (tentativa %d)" % (nome, r.status_code, tentativa + 1))
        except Exception as e:
            print("  [%s] erro: %s (tentativa %d)" % (nome, e, tentativa + 1))
        time.sleep(3)
    else:
        print("  [%s] FALHOU apos 3 tentativas" % nome)
        return []

    feed = feedparser.parse(r.content)
    itens = []

    for entrada in feed.entries:
        video_id = entrada.get("yt_videoid")
        if not video_id:
            continue

        try:
            publicado = datetime(*entrada.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            continue

        itens.append({
            "id": video_id,
            "titulo": entrada.get("title", "(sem titulo)"),
            "canal": nome,
            "publicado": publicado,
            "link": "https://www.youtube.com/watch?v=" + video_id,
        })

    return itens


def _monta_regex(termos):
    corpo = "|".join(re.escape(t) for t in termos)
    return re.compile(r"(?<![\w-])(" + corpo + r")(?![\w-])", re.I)


REGEX_FORTE = _monta_regex(MARCADORES)
REGEX_FRACO = _monta_regex(MARCADORES_FRACOS)


def passa_no_filtro(titulo):
    if not SO_BREAKING:
        return True
    if REGEX_FORTE.search(titulo):
        return True
    if USAR_FRACOS and REGEX_FRACO.search(titulo):
        return True
    return False


def formatar_idade(publicado, agora):
    minutos = int((agora - publicado).total_seconds() / 60)
    if minutos < 1:
        return "agora"
    if minutos < 60:
        return "ha %d min" % minutos
    return "ha %dh%02d" % (minutos // 60, minutos % 60)


def enviar_telegram(item, agora):
    texto = (
        "\U0001F534 <b>%s</b>\n\n"
        "%s\n\n"
        "\U0001F551 %s\n"
        "%s"
    ) % (
        html.escape(item["canal"]),
        html.escape(item["titulo"]),
        formatar_idade(item["publicado"], agora),
        item["link"],
    )

    url = "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        r = requests.post(url, data=payload, timeout=25)
        if r.status_code != 200:
            print("  Telegram HTTP %s: %s" % (r.status_code, r.text[:200]))
            return False
        return True
    except Exception as e:
        print("  Telegram erro: %s" % e)
        return False


def main():
    agora = datetime.now(timezone.utc)
    vistos = carregar_vistos()
    primeira_vez = len(vistos) == 0

    print("=" * 60)
    print("FOX BREAKING - %s UTC" % agora.strftime("%Y-%m-%d %H:%M"))
    print("Ja vistos: %d | Filtro: %s" % (len(vistos), "SO BREAKING" if SO_BREAKING else "TUDO"))
    print("=" * 60)

    todos = []
    for channel_id, nome in CANAIS.items():
        print("Lendo: %s" % nome)
        itens = buscar_canal(channel_id, nome)
        print("  %d videos no feed" % len(itens))
        todos.extend(itens)
        time.sleep(1.5)

    todos.sort(key=lambda x: x["publicado"])

    if primeira_vez:
        ids = [i["id"] for i in todos]
        salvar_vistos(ids)
        print("\nPRIMEIRA EXECUCAO: %d videos gravados sem enviar." % len(ids))
        print("A partir de agora so o que for novo sera enviado.")
        return

    limite = agora - timedelta(hours=IDADE_MAXIMA_HORAS)
    enviados = 0

    for item in todos:
        if item["id"] in vistos:
            continue

        vistos.append(item["id"])

        if item["publicado"] < limite:
            print("  [velho] %s" % item["titulo"][:60])
            continue

        if not passa_no_filtro(item["titulo"]):
            print("  [filtrado] %s" % item["titulo"][:60])
            continue

        if enviar_telegram(item, agora):
            enviados += 1
            print("  [ENVIADO] %s | %s" % (item["canal"], item["titulo"][:60]))
            time.sleep(1.2)

    salvar_vistos(vistos)
    print("\nFIM: %d enviados." % enviados)


if __name__ == "__main__":
    main()
