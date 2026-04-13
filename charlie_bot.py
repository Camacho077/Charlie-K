"""
Charlie Bot v5 — Google Gemini
=======================================================
pip install google-genai python-telegram-bot pillow
"""

import io
import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    raise ImportError("pip install google-genai")

try:
    from telegram import Update, InputFile
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, MessageHandler,
        filters, ContextTypes
    )
except ImportError:
    raise ImportError("pip install python-telegram-bot")

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

from config import TELEGRAM_TOKEN, GEMINI_KEY
from perfil_usuario import PerfilUsuario

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Charlie")

# ─── INICIALIZACIÓN ───────────────────────────────────────────────────────────
cliente_gemini  = genai.Client(api_key=GEMINI_KEY)
MODELO_GEMINI   = "gemini-2.5-flash"
MODELO_RAPIDO   = "gemini-2.0-flash"
gestor_perfiles = PerfilUsuario()

# ─── CONSTANTES ───────────────────────────────────────────────────────────────
MAX_HISTORIAL    = 30   # memoria larga — guarda hasta 30 turnos
MAX_NOTAS        = 20   # notas importantes recordadas por Charlie

EMOCIONES_ES = {
    "sadness": "tristeza", "joy": "alegría", "love": "amor/cariño",
    "anger":   "enojo",    "fear": "miedo",  "surprise": "sorpresa",
}
EMOJIS_EMO = {
    "sadness": "😔", "joy": "😄", "love": "❤️",
    "anger":   "😤", "fear": "😨", "surprise": "😲",
}

# ─── DETECCIÓN DE EMOCIÓN (local, sin API extra) ──────────────────────────────

def detectar_emocion(texto: str) -> dict:
    t = texto.lower()
    KEYWORDS = {
        "sadness": ["triste","llorando","deprimido","solo","mal","fatal","peor","llorar","dolor","pena","angustia"],
        "anger":   ["rabia","enojo","enojado","molesto","furioso","harto","odio","ira","fastidio","bronca"],
        "fear":    ["miedo","asustado","nervioso","ansioso","angustia","preocupado","temor","pánico"],
        "love":    ["amor","quiero","enamorado","cariño","feliz contigo","hermoso","te amo","amo"],
        "surprise":["sorpresa","increíble","no puedo creer","wow","alucinante","impresionante","qué!"],
        "joy":     ["feliz","contento","genial","excelente","bien","alegre","emocionado","chévere","bacano","buenísimo"],
    }
    en = "joy"
    for emocion, palabras in KEYWORDS.items():
        if any(p in t for p in palabras):
            en = emocion
            break
    palabras_txt = texto.split()
    pct_mayus = sum(1 for p in palabras_txt if p.isupper()) / max(len(palabras_txt), 1)
    return {
        "en": en, "es": EMOCIONES_ES.get(en, en),
        "emoji": EMOJIS_EMO.get(en, ""), "confianza": 0.85,
        "intensidad": "alta" if pct_mayus > 0.3 else "normal",
    }

# ─── EXTRACCIÓN DE DATOS ──────────────────────────────────────────────────────

def extraer_datos(texto: str) -> dict:
    datos = {}
    STOP = {"tu","un","una","el","la","su","yo","programador","estudiante",
            "developer","creador","bot","ia","robot"}

    for pat in [r"me llamo\s+(\w+)", r"mi nombre es\s+(\w+)",
                r"llámame\s+(\w+)", r"soy\s+([A-Z][a-z]{2,})\b"]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            cand = m.group(1).capitalize()
            if cand.lower() not in STOP:
                datos["nombre"] = cand
                break

    gustos = []
    for pat in [r"me gusta[n]?\s+(.+?)(?:\.|,|y |$)",
                r"me encanta[n]?\s+(.+?)(?:\.|,|y |$)",
                r"amo\s+(.+?)(?:\.|,|y |$)",
                r"soy fan de\s+(.+?)(?:\.|,|$)"]:
        for m in re.finditer(pat, texto, re.IGNORECASE):
            g = m.group(1).strip().rstrip(".,; ")
            if g and len(g) < 60:
                gustos.append(g)
    if gustos:
        datos["gustos_nuevos"] = gustos

    m = re.search(r"tengo\s+(\d{1,2})\s+años", texto, re.IGNORECASE)
    if m:
        datos["edad"] = int(m.group(1))

    for pat in [r"trabajo\s+(?:como|de|en)\s+(.+?)(?:\.|,|$)",
                r"estudio\s+(.+?)(?:\.|,|$)"]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            datos["ocupacion"] = m.group(1).strip().rstrip(".,; ")
            break

    for pat in [r"vivo en\s+(.+?)(?:\.|,|$)", r"soy de\s+(.+?)(?:\.|,|$)"]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            datos["lugar"] = m.group(1).strip().rstrip(".,; ")
            break

    for pat in [r"hoy (?:me siento|estoy)\s+(.+?)(?:\.|,|$)",
                r"me encuentro\s+(.+?)(?:\.|,|$)"]:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            datos["ultimo_estado_animo"] = m.group(1).strip().rstrip(".,; ")
            break

    return datos

# ─── DETECCIÓN DE INTENCIÓN ───────────────────────────────────────────────────

def detectar_intencion(texto: str) -> str:
    t = texto.lower()
    if any(k in t for k in ["chiste","hazme reír","algo gracioso"]):
        return "chiste"
    if any(k in t for k in ["consejo","qué hago","ayúdame a decidir","qué harías"]):
        return "consejo"
    if any(k in t for k in ["dato curioso","algo interesante","dime algo","cuéntame algo"]):
        return "dato_curioso"
    if any(k in t for k in ["estoy triste","me siento mal","no estoy bien","estoy llorando","estoy deprimido"]):
        return "apoyo_emocional"
    if any(k in t for k in ["me aburro","estoy aburrido","qué hago ahora","juguemos"]):
        return "entretenimiento"
    if any(k in t for k in ["eres ia","eres bot","eres robot","eres humano","eres real"]):
        return "identidad"
    if any(k in t for k in ["qué sabes de mí","que sabes de mi","qué recuerdas","que recuerdas","cuéntame de mí"]):
        return "pedir_perfil"
    if any(k in t for k in ["cómo me he sentido","mis emociones","mi estado emocional","análisis emocional"]):
        return "analisis_emocional"
    if any(k in t for k in ["recomiéndame","recomiendame","qué veo","qué leo","película","serie","canción","libro"]):
        return "recomendacion"
    if any(k in t for k in ["reflexión","frase del día","frase motivadora","motívame","motivame"]):
        return "reflexion"
    # Detección de consulta sobre otro usuario: "infografía de X", "info de X", "qué sabes de X"
    if re.search(r"(infograf[íi]a|info|perfil|qu[eé] sabes)\s+(de|sobre)\s+(\w+)", t):
        return "consulta_usuario"
    if re.match(r"^(hola|hey|buenas|qué tal|ey|hi|buenos días|buenas tardes|buenas noches)[!?.]*$", t.strip()):
        return "saludo"
    return "conversacion"

# ─── INSTRUCCIONES ────────────────────────────────────────────────────────────

INSTRUCCION = {
    "chiste":             "Cuenta un chiste original, divertido y bien construido. Termina preguntando si quiere otro.",
    "consejo":            "Da UN consejo directo, práctico y empático. Sin rodeos, bien explicado.",
    "dato_curioso":       "Comparte un dato curioso sorprendente, preferiblemente sobre los gustos del usuario.",
    "apoyo_emocional":    "NO des consejos. Escucha, valida sus sentimientos con calidez, haz UNA pregunta empática.",
    "entretenimiento":    "Propón algo concreto: adivinanza, juego de palabras, pregunta absurda o reto mental.",
    "identidad":          "Eres Charlie, un bot con personalidad real. Responde con honestidad, humor y carácter.",
    "saludo":             "Saluda con energía. Si ya se conocen, menciona algo específico que recuerdes de él/ella.",
    "pedir_perfil":       "Resume todo lo que sabes del usuario de forma cálida y conversacional. No como lista fría.",
    "analisis_emocional": "Analiza sus emociones históricas de forma profunda y empática. Identifica patrones. Habla como amigo.",
    "recomendacion":      "Recomienda algo específico (película/serie/libro/canción) basado en sus gustos. Explica por qué.",
    "reflexion":          "Comparte una reflexión genuina adaptada al estado emocional del usuario. Nada de clichés.",
    "consulta_usuario":   "El administrador pide info de otro usuario. Revisa el perfil disponible y preséntalo completo.",
    "conversacion":       "Conversación natural. Sé curioso y cálido. Si no preguntó nada, termina con UNA pregunta personal.",
}

# ─── PROMPT ───────────────────────────────────────────────────────────────────

def construir_prompt(texto: str, historial: list, perfil: dict,
                     emocion: dict, intencion: str, perfil_consultado: dict = None) -> str:
    nombre     = perfil.get("nombre", "amigo")
    gustos     = ", ".join(perfil.get("gustos", [])) or "desconocidos"
    ocupacion  = perfil.get("ocupacion", "desconocida")
    edad       = perfil.get("edad", "desconocida")
    lugar      = perfil.get("lugar", "desconocido")
    n_msgs     = perfil.get("total_mensajes", 0)
    racha      = perfil.get("racha_dias", 1)
    emo_hist   = perfil.get("emociones_frecuentes", {})
    emo_top    = max(emo_hist, key=emo_hist.get) if emo_hist else "joy"
    notas      = perfil.get("notas_importantes", [])
    notas_str  = " | ".join(notas[-5:]) if notas else "ninguna aún"

    if emo_hist:
        total_emo   = sum(emo_hist.values())
        resumen_emo = ", ".join(
            f"{EMOCIONES_ES.get(e,e)}({c/total_emo*100:.0f}%)"
            for e, c in sorted(emo_hist.items(), key=lambda x: -x[1])
        )
    else:
        resumen_emo = "sin datos"

    # Si es consulta de otro usuario, agregar su perfil
    extra = ""
    if perfil_consultado:
        extra = f"\nPERFIL CONSULTADO:\n{json.dumps(perfil_consultado, ensure_ascii=False, indent=2)}\n"

    prompt = (
        f"Eres Charlie: chatbot amigo, natural, empático, directo, con humor y muy buena memoria.\n"
        f"USUARIO: {nombre} | {edad}a | {lugar} | {ocupacion} | gustos: {gustos}\n"
        f"EMOCIONES: frecuente={EMOCIONES_ES.get(emo_top,emo_top)} | historial={resumen_emo}\n"
        f"NOTAS CLAVE: {notas_str}\n"
        f"AHORA: emoción={emocion['es']}{emocion['emoji']} | msgs={n_msgs} | racha={racha}d\n"
        f"INSTRUCCIÓN: {INSTRUCCION.get(intencion, INSTRUCCION['conversacion'])}\n"
        f"{extra}\n"
        f"REGLAS ABSOLUTAS:\n"
        f"- Responde en español latinoamericano informal y fluido.\n"
        f"- Escribe entre 2 y 4 oraciones completas. NUNCA dejes una oración incompleta.\n"
        f"- Cada oración DEBE terminar en punto, signo de exclamación o pregunta.\n"
        f"- Sin listas, sin asteriscos, sin markdown de ningún tipo.\n"
        f"- Si haces pregunta, solo UNA al final.\n"
        f"- Si llevan más de 10 mensajes, menciona algo específico que recuerdes.\n"
    )

    if historial:
        prompt += "\nHISTORIAL RECIENTE:\n"
        for t in historial[-MAX_HISTORIAL:]:
            prompt += f"U: {t['usuario']}\nC: {t['charlie']}\n"

    prompt += f"\nUsuario: {texto}\nCharlie:"
    return prompt

# ─── LLAMADA A GEMINI CON REINTENTOS Y FALLBACK ───────────────────────────────

def llamar_gemini(texto: str, historial: list, perfil: dict,
                  emocion: dict, intencion: str, perfil_consultado: dict = None) -> str:
    prompt = construir_prompt(texto, historial, perfil, emocion, intencion, perfil_consultado)
    logger.info(f"-> Gemini | {emocion['es']} | {intencion}")

    for modelo in [MODELO_GEMINI, MODELO_RAPIDO]:
        for intento in range(3):
            try:
                resp = cliente_gemini.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=800,
                        temperature=0.82,
                    )
                )
                texto_resp = resp.text.strip()
                if texto_resp:
                    if texto_resp[-1] not in ".!?…)\"'":
                        texto_resp += "."
                    logger.info(f"<- OK [{modelo}] {len(texto_resp)}c")
                    return texto_resp
            except Exception as e:
                msg = str(e)
                logger.warning(f"[{modelo}] intento {intento+1}: {msg[:80]}")
                if "503" in msg or "UNAVAILABLE" in msg:
                    time.sleep(2 * (intento + 1))
                    continue
                if "429" in msg or "quota" in msg.lower():
                    return "Llegué al límite por ahora. Espera unos minutos e inténtalo de nuevo."
                if "SAFETY" in msg or "blocked" in msg.lower():
                    return "Ese mensaje no lo pude procesar. Inténtalo de otra forma."
                if "404" in msg:
                    break
                return "Algo salió mal ahora mismo. Inténtalo de nuevo en un momento."

    return "Estoy con mucha demanda ahora mismo. Dame un minuto e inténtalo de nuevo."

# ─── NOTAS AUTOMÁTICAS (memoria semántica) ────────────────────────────────────

def actualizar_notas(uid: int, texto: str, perfil: dict):
    """Detecta info importante y la guarda como nota corta."""
    TRIGGERS = [
        (r"(mi (?:mejor amigo|pareja|novio|novia|esposo|esposa|mamá|papá|hermano|hermana)[^.]+)", "relación"),
        (r"(trabajo(?:ndo)? en[^.]+)", "trabajo"),
        (r"(mi sueño es[^.]+)", "sueño"),
        (r"(mi mayor miedo es[^.]+)", "miedo"),
        (r"(estoy pasando por[^.]+)", "situación"),
        (r"(me diagnosticaron[^.]+)", "salud"),
        (r"(cumpleaños[^.]+)", "fecha"),
    ]
    notas = perfil.get("notas_importantes", [])
    for pat, _ in TRIGGERS:
        m = re.search(pat, texto, re.IGNORECASE)
        if m:
            nota = m.group(1).strip().rstrip(".,; ")[:80]
            if nota and nota not in notas:
                notas.append(nota)
    gestor_perfiles.actualizar(uid, {"notas_importantes": notas[-MAX_NOTAS:]})

# ─── INFOGRAFÍA ───────────────────────────────────────────────────────────────

def generar_infografia(perfil: dict) -> bytes:
    """Genera una imagen PNG con el perfil del usuario."""
    W, H = 800, 600
    BG       = (18, 18, 30)
    ACCENT   = (99, 102, 241)
    CARD     = (30, 30, 50)
    WHITE    = (255, 255, 255)
    GRAY     = (160, 160, 180)
    GREEN    = (52, 211, 153)
    YELLOW   = (251, 191, 36)
    RED      = (248, 113, 113)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Intentar cargar fuente, fallback a default
    try:
        font_big   = ImageFont.truetype("arial.ttf", 28)
        font_med   = ImageFont.truetype("arial.ttf", 20)
        font_small = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font_big = font_med = font_small = ImageFont.load_default()

    nombre    = perfil.get("nombre", "Usuario")
    edad      = perfil.get("edad", "—")
    lugar     = perfil.get("lugar", "—")
    ocupacion = perfil.get("ocupacion", "—")
    gustos    = ", ".join(perfil.get("gustos", [])) or "—"
    n_msgs    = perfil.get("total_mensajes", 0)
    racha     = perfil.get("racha_dias", 1)
    desde     = perfil.get("primer_mensaje", "—")
    emo_hist  = perfil.get("emociones_frecuentes", {})
    notas     = perfil.get("notas_importantes", [])

    # Header
    draw.rectangle([0, 0, W, 80], fill=ACCENT)
    draw.text((30, 20), f"PERFIL — {nombre.upper()}", font=font_big, fill=WHITE)

    # Info cards fila 1
    campos = [
        ("🎂 Edad", str(edad)),
        ("📍 Lugar", str(lugar)),
        ("💼 Ocupación", str(ocupacion)[:20]),
    ]
    for i, (label, valor) in enumerate(campos):
        x = 20 + i * 260
        draw.rounded_rectangle([x, 100, x+240, 175], radius=12, fill=CARD)
        draw.text((x+12, 108), label, font=font_small, fill=GRAY)
        draw.text((x+12, 132), valor, font=font_med, fill=WHITE)

    # Gustos
    draw.rounded_rectangle([20, 190, W-20, 255], radius=12, fill=CARD)
    draw.text((32, 198), "❤️ Le gusta", font=font_small, fill=GRAY)
    draw.text((32, 220), gustos[:70], font=font_med, fill=WHITE)

    # Stats
    stats = [
        ("💬 Mensajes", str(n_msgs), GREEN),
        ("🔥 Racha", f"{racha} días", YELLOW),
        ("📅 Desde", str(desde), GRAY),
    ]
    for i, (label, valor, color) in enumerate(stats):
        x = 20 + i * 260
        draw.rounded_rectangle([x, 270, x+240, 345], radius=12, fill=CARD)
        draw.text((x+12, 278), label, font=font_small, fill=GRAY)
        draw.text((x+12, 302), valor, font=font_med, fill=color)

    # Emociones
    draw.rounded_rectangle([20, 360, W//2-10, 530], radius=12, fill=CARD)
    draw.text((32, 368), "🎭 Emociones", font=font_small, fill=GRAY)
    if emo_hist:
        total = sum(emo_hist.values())
        y_e = 392
        for en, cnt in sorted(emo_hist.items(), key=lambda x: -x[1])[:5]:
            pct = cnt / total
            emoji = EMOJIS_EMO.get(en, "")
            nombre_emo = EMOCIONES_ES.get(en, en)
            draw.text((32, y_e), f"{emoji} {nombre_emo}", font=font_small, fill=WHITE)
            bar_w = int(pct * 270)
            draw.rounded_rectangle([170, y_e+2, 170+bar_w, y_e+14], radius=4, fill=ACCENT)
            draw.text((170+bar_w+6, y_e), f"{pct*100:.0f}%", font=font_small, fill=GRAY)
            y_e += 26
    else:
        draw.text((32, 400), "Sin datos aún.", font=font_small, fill=GRAY)

    # Notas
    draw.rounded_rectangle([W//2+10, 360, W-20, 530], radius=12, fill=CARD)
    draw.text((W//2+22, 368), "📝 Notas clave", font=font_small, fill=GRAY)
    y_n = 392
    for nota in notas[-5:]:
        draw.text((W//2+22, y_n), f"• {nota[:30]}", font=font_small, fill=WHITE)
        y_n += 24
    if not notas:
        draw.text((W//2+22, 400), "Sin notas aún.", font=font_small, fill=GRAY)

    # Footer
    draw.rectangle([0, 560, W, H], fill=ACCENT)
    draw.text((30, 570), "Charlie Bot — Perfil generado automáticamente", font=font_small, fill=WHITE)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def guardar_turno(user_id: int, historial: list, texto: str, respuesta: str):
    historial.append({"usuario": texto, "charlie": respuesta})
    gestor_perfiles.actualizar(user_id, {"historial": historial[-MAX_HISTORIAL:]})

def calcular_racha(perfil: dict) -> int:
    hoy    = date.today().isoformat()
    ultimo = perfil.get("ultimo_dia_activo", "")
    racha  = perfil.get("racha_dias", 0)
    if ultimo == hoy:
        return racha
    ayer = (date.today() - timedelta(days=1)).isoformat()
    return racha + 1 if ultimo == ayer else 1

def emo_neutral():
    return {"en":"joy","es":"alegría","emoji":"😄","confianza":0.9,"intensidad":"normal"}

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid       = update.effective_user.id
    tg_nombre = update.effective_user.first_name or "amigo"
    perfil    = gestor_perfiles.obtener(uid)

    if not perfil.get("nombre"):
        gestor_perfiles.actualizar(uid, {
            "nombre": tg_nombre,
            "primer_mensaje": date.today().isoformat(),
        })
        msg = (
            f"¡Hola, {tg_nombre}! Soy Charlie, tu compañero de conversación.\n\n"
            f"Voy a recordar todo lo que me cuentes, notar cómo te sentís y aprender "
            f"sobre vos poco a poco. ¿Cómo estás hoy?\n\n"
            f"Comandos: /perfil · /yo · /estado · /chiste · /dato · /frase · /resetear · /ayuda"
        )
    else:
        n      = perfil.get("total_mensajes", 0)
        racha  = perfil.get("racha_dias", 1)
        gustos = perfil.get("gustos", [])
        detalle = f"Sigo recordando que te gusta {gustos[0]}." if gustos else "¡Tenemos mucho de qué hablar!"
        msg = (
            f"¡Bienvenido de nuevo, {tg_nombre}! Qué bueno leerte.\n\n"
            f"Llevamos {n} mensajes juntos y {racha} día(s) seguidos hablando. {detalle} "
            f"¿Cómo has estado?"
        )
    await update.message.reply_text(msg)


async def cmd_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    p        = gestor_perfiles.obtener(uid)
    emo_hist = p.get("emociones_frecuentes", {})
    emo_top  = max(emo_hist, key=emo_hist.get) if emo_hist else None
    emo_str  = f"{EMOJIS_EMO.get(emo_top,'')} {EMOCIONES_ES.get(emo_top,'—')}" if emo_top else "—"
    gustos_str = ", ".join(p.get("gustos", [])) or "—"
    notas    = p.get("notas_importantes", [])
    notas_str = "\n".join(f"  • {n}" for n in notas[-5:]) or "  —"

    await update.message.reply_text(
        f"👤 Perfil de {p.get('nombre','—')}\n\n"
        f"🎂 Edad: {p.get('edad','—')}\n"
        f"📍 Lugar: {p.get('lugar','—')}\n"
        f"💼 Ocupación: {p.get('ocupacion','—')}\n"
        f"❤️ Le gusta: {gustos_str}\n\n"
        f"💬 Mensajes: {p.get('total_mensajes',0)}\n"
        f"📅 Desde: {p.get('primer_mensaje','—')}\n"
        f"🔥 Racha: {p.get('racha_dias',1)} día(s)\n"
        f"🎭 Emoción frecuente: {emo_str}\n\n"
        f"📝 Notas clave:\n{notas_str}"
    )


async def cmd_yo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    perfil = gestor_perfiles.obtener(uid)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    historial = perfil.get("historial", [])
    r = llamar_gemini(
        "Cuéntame todo lo que sabes de mí, qué has aprendido y cómo me percibes como persona.",
        historial, perfil, emo_neutral(), "pedir_perfil"
    )
    guardar_turno(uid, historial, "/yo", r)
    await update.message.reply_text(r)


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    p        = gestor_perfiles.obtener(uid)
    emo_hist = p.get("emociones_frecuentes", {})

    if not emo_hist:
        await update.message.reply_text("Aún no tengo suficientes datos. ¡Sigue hablando conmigo!")
        return

    total  = sum(emo_hist.values())
    lineas = []
    for en, cnt in sorted(emo_hist.items(), key=lambda x: -x[1]):
        pct   = cnt / total * 100
        barra = "█" * max(1, int(pct / 8))
        lineas.append(f"{EMOJIS_EMO.get(en,'')} {EMOCIONES_ES.get(en,en):<14} {barra} {pct:.0f}%")

    historial = p.get("historial", [])
    analisis  = llamar_gemini(
        "Dame un análisis empático y detallado de cómo me he sentido emocionalmente según nuestras conversaciones.",
        historial, p, emo_neutral(), "analisis_emocional"
    )

    await update.message.reply_text(
        f"📊 Estado emocional de {p.get('nombre','ti')} ({total} mensajes)\n\n"
        + "\n".join(lineas)
        + f"\n\n💬 {analisis}"
    )


async def cmd_infografia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genera infografía del propio usuario o de otro si se pasa el nombre."""
    uid  = update.effective_user.id
    args = context.args

    if args:
        # Buscar usuario por nombre en todos los perfiles
        nombre_buscado = " ".join(args).strip().lower()
        perfil_encontrado = None
        uid_encontrado    = None

        ruta = Path("data/perfiles.json")
        if ruta.exists():
            with open(ruta, "r", encoding="utf-8") as f:
                todos = json.load(f)
            for u_id, p in todos.items():
                if p.get("nombre","").lower() == nombre_buscado:
                    perfil_encontrado = p
                    uid_encontrado    = u_id
                    break

        if not perfil_encontrado:
            await update.message.reply_text(
                f"No encontré ningún usuario con el nombre '{' '.join(args)}'."
            )
            return
        perfil_img = perfil_encontrado
    else:
        perfil_img = gestor_perfiles.obtener(uid)

    if not PIL_OK:
        await update.message.reply_text(
            "Falta la librería PIL para generar imágenes.\n"
            "Instálala con: pip install pillow"
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
    img_bytes = generar_infografia(perfil_img)
    nombre = perfil_img.get("nombre", "usuario")
    await update.message.reply_photo(
        photo=InputFile(io.BytesIO(img_bytes), filename=f"perfil_{nombre}.png"),
        caption=f"📊 Infografía de {nombre}"
    )


async def cmd_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el perfil completo de un usuario por nombre (para el admin)."""
    if not context.args:
        await update.message.reply_text("Uso: /usuario <nombre>\nEjemplo: /usuario Juan")
        return

    nombre_buscado = " ".join(context.args).strip().lower()
    ruta = Path("data/perfiles.json")

    if not ruta.exists():
        await update.message.reply_text("No hay perfiles guardados aún.")
        return

    with open(ruta, "r", encoding="utf-8") as f:
        todos = json.load(f)

    perfil_enc = None
    for u_id, p in todos.items():
        if p.get("nombre","").lower() == nombre_buscado:
            perfil_enc = p
            break

    if not perfil_enc:
        await update.message.reply_text(f"No encontré a nadie con el nombre '{' '.join(context.args)}'.")
        return

    emo_hist = perfil_enc.get("emociones_frecuentes", {})
    emo_top  = max(emo_hist, key=emo_hist.get) if emo_hist else None
    emo_str  = f"{EMOJIS_EMO.get(emo_top,'')} {EMOCIONES_ES.get(emo_top,'—')}" if emo_top else "—"
    total    = sum(emo_hist.values()) if emo_hist else 0
    lineas_emo = []
    for en, cnt in sorted(emo_hist.items(), key=lambda x: -x[1]):
        pct   = cnt / total * 100 if total else 0
        barra = "█" * max(1, int(pct / 10))
        lineas_emo.append(f"  {EMOJIS_EMO.get(en,'')} {EMOCIONES_ES.get(en,en):<12} {barra} {pct:.0f}%")

    gustos_str = ", ".join(perfil_enc.get("gustos", [])) or "—"
    notas      = perfil_enc.get("notas_importantes", [])
    notas_str  = "\n".join(f"  • {n}" for n in notas) or "  —"
    n_msgs     = perfil_enc.get("total_mensajes", 0)
    historial  = perfil_enc.get("historial", [])
    ultimo_msg = historial[-1]["usuario"] if historial else "—"

    await update.message.reply_text(
        f"👤 Perfil de {perfil_enc.get('nombre','—')}\n\n"
        f"🎂 Edad: {perfil_enc.get('edad','—')}\n"
        f"📍 Lugar: {perfil_enc.get('lugar','—')}\n"
        f"💼 Ocupación: {perfil_enc.get('ocupacion','—')}\n"
        f"❤️ Le gusta: {gustos_str}\n\n"
        f"💬 Mensajes totales: {n_msgs}\n"
        f"📅 Desde: {perfil_enc.get('primer_mensaje','—')}\n"
        f"🔥 Racha: {perfil_enc.get('racha_dias',1)} día(s)\n"
        f"🕐 Último mensaje: {ultimo_msg[:60]}\n\n"
        f"🎭 Emoción más frecuente: {emo_str}\n"
        f"📊 Distribución emocional ({total} msgs):\n"
        + "\n".join(lineas_emo) +
        f"\n\n📝 Notas clave:\n{notas_str}\n\n"
        f"(Usa /infografia {perfil_enc.get('nombre','')} para la versión visual)"
    )


async def cmd_resetear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gestor_perfiles.eliminar(update.effective_user.id)
    await update.message.reply_text("Borré todo lo que sabía de ti. Empezamos de cero.\n¿Cómo te llamas?")


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Charlie v5 — comandos\n\n"
        "/yo              — qué sé de ti\n"
        "/perfil          — tu ficha completa\n"
        "/estado          — tu resumen emocional\n"
        "/infografia      — tu perfil en imagen\n"
        "/infografia Nombre — perfil de otro usuario\n"
        "/usuario Nombre  — datos completos de otro usuario\n"
        "/chiste          — un chiste\n"
        "/dato            — dato curioso personalizado\n"
        "/frase           — reflexión motivadora\n"
        "/resetear        — borrar mi memoria de ti\n"
        "/ayuda           — este mensaje\n\n"
        "También puedes pedirme consejos, recomendaciones de películas, "
        "series, libros o música. ¡Estoy aquí!"
    )


async def cmd_chiste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    perfil = gestor_perfiles.obtener(uid)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    historial = perfil.get("historial", [])
    r = llamar_gemini("Cuéntame un chiste original y divertido.", historial, perfil, emo_neutral(), "chiste")
    guardar_turno(uid, historial, "/chiste", r)
    await update.message.reply_text(r)


async def cmd_dato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    perfil = gestor_perfiles.obtener(uid)
    gustos = ", ".join(perfil.get("gustos", [])) or "cualquier tema"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    historial = perfil.get("historial", [])
    r = llamar_gemini(
        f"Dame un dato curioso y sorprendente sobre: {gustos}.",
        historial, perfil,
        {"en":"surprise","es":"sorpresa","emoji":"😲","confianza":0.95,"intensidad":"normal"},
        "dato_curioso"
    )
    guardar_turno(uid, historial, "/dato", r)
    await update.message.reply_text(r)


async def cmd_frase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    perfil = gestor_perfiles.obtener(uid)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    historial = perfil.get("historial", [])
    r = llamar_gemini(
        "Dame una reflexión o frase motivadora genuina adaptada a lo que sabes de mí.",
        historial, perfil, emo_neutral(), "reflexion"
    )
    guardar_turno(uid, historial, "/frase", r)
    await update.message.reply_text(r)


# ─── HANDLER PRINCIPAL ────────────────────────────────────────────────────────

async def manejar_mensaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    texto = update.message.text.strip()
    if not texto:
        return

    perfil = gestor_perfiles.obtener(uid)
    n      = perfil.get("total_mensajes", 0)

    if n == 0:
        gestor_perfiles.actualizar(uid, {
            "primer_mensaje": date.today().isoformat(),
            "nombre": update.effective_user.first_name or "amigo",
        })
        perfil = gestor_perfiles.obtener(uid)

    nueva_racha = calcular_racha(perfil)
    gestor_perfiles.actualizar(uid, {
        "racha_dias": nueva_racha,
        "ultimo_dia_activo": date.today().isoformat(),
    })

    emocion   = detectar_emocion(texto)
    intencion = detectar_intencion(texto)
    logger.info(f"[{uid}] {emocion['es']} | {intencion}")

    emo_hist = perfil.get("emociones_frecuentes", {})
    emo_hist[emocion["en"]] = emo_hist.get(emocion["en"], 0) + 1
    gestor_perfiles.actualizar(uid, {
        "emociones_frecuentes": emo_hist,
        "ultima_emocion":       emocion["en"],
        "total_mensajes":       n + 1,
    })

    datos = extraer_datos(texto)
    if datos:
        gustos_nuevos = datos.pop("gustos_nuevos", [])
        if gustos_nuevos:
            datos["gustos"] = list(set(perfil.get("gustos", []) + gustos_nuevos))
        gestor_perfiles.actualizar(uid, datos)
        perfil = gestor_perfiles.obtener(uid)

    # Guardar notas importantes automáticamente
    actualizar_notas(uid, texto, perfil)
    perfil = gestor_perfiles.obtener(uid)
    perfil["racha_dias"] = nueva_racha

    # Si pide infografía de alguien en texto libre
    m_info = re.search(r"infograf[íi]a\s+(?:de|sobre)\s+(\w+)", texto, re.IGNORECASE)
    if m_info:
        context.args = [m_info.group(1)]
        await cmd_infografia(update, context)
        return

    # Si pide info de otro usuario en texto libre
    m_usu = re.search(r"(?:info|perfil|qu[eé] sabes)\s+(?:de|sobre)\s+(\w+)", texto, re.IGNORECASE)
    perfil_consultado = None
    if m_usu and intencion == "consulta_usuario":
        nombre_bus = m_usu.group(1).lower()
        ruta = Path("data/perfiles.json")
        if ruta.exists():
            with open(ruta, "r", encoding="utf-8") as f:
                todos = json.load(f)
            for _, p in todos.items():
                if p.get("nombre","").lower() == nombre_bus:
                    perfil_consultado = p
                    break

    historial = perfil.get("historial", [])
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    respuesta = llamar_gemini(texto, historial, perfil, emocion, intencion, perfil_consultado)
    guardar_turno(uid, historial, texto, respuesta)
    await update.message.reply_text(respuesta)


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)


# ─── VERIFICACIÓN ─────────────────────────────────────────────────────────────

def verificar_config() -> bool:
    sep = "=" * 54
    print(f"\n{sep}")
    print("  Verificando configuración de Charlie v5...")
    print(sep)

    if not TELEGRAM_TOKEN or "PEGA" in TELEGRAM_TOKEN:
        print("  ERROR: TELEGRAM_TOKEN no configurado en config.py")
        return False
    print(f"  OK: TELEGRAM_TOKEN  ...{TELEGRAM_TOKEN[-8:]}")

    if not GEMINI_KEY or "PEGA" in GEMINI_KEY:
        print("  ERROR: GEMINI_KEY no configurada en config.py")
        return False
    print(f"  OK: GEMINI_KEY      ...{GEMINI_KEY[-6:]}")

    if PIL_OK:
        print("  OK: Pillow instalado — infografías disponibles.")
    else:
        print("  AVISO: Pillow no instalado. Las infografías no funcionarán.")
        print("         Instala con: pip install pillow")

    print(f"  Probando Gemini ({MODELO_GEMINI})...")
    for modelo in [MODELO_GEMINI, MODELO_RAPIDO]:
        try:
            test = cliente_gemini.models.generate_content(
                model=modelo, contents="Responde solo: OK"
            )
            print(f"  OK: [{modelo}] responde -> '{test.text.strip()}'")
            break
        except Exception as e:
            msg = str(e)
            print(f"  FALLO [{modelo}]: {msg[:120]}")
            if "503" in msg:
                print("       Modelo saturado, se usará el siguiente.")
            elif "404" in msg:
                print("       Modelo no disponible para esta key.")
            elif "429" in msg:
                print("       Cuota agotada.")
            elif "API_KEY_INVALID" in msg:
                print("       Key inválida. Ve a https://aistudio.google.com/app/apikey")
                return False
    else:
        print("  ERROR: Ningún modelo disponible. Revisa tu API key.")
        return False

    print(sep)
    print("  Todo OK. Charlie v5 iniciando...\n")
    return True


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not verificar_config():
        print("\nCorrige la configuración antes de iniciar.\n")
        exit(1)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("perfil",      cmd_perfil))
    app.add_handler(CommandHandler("yo",          cmd_yo))
    app.add_handler(CommandHandler("estado",      cmd_estado))
    app.add_handler(CommandHandler("infografia",  cmd_infografia))
    app.add_handler(CommandHandler("usuario",     cmd_usuario))
    app.add_handler(CommandHandler("resetear",    cmd_resetear))
    app.add_handler(CommandHandler("ayuda",       cmd_ayuda))
    app.add_handler(CommandHandler("chiste",      cmd_chiste))
    app.add_handler(CommandHandler("dato",        cmd_dato))
    app.add_handler(CommandHandler("frase",       cmd_frase))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
    app.add_error_handler(manejar_error)

    print("Charlie v5 en línea. Ctrl+C para detener.\n")
    app.run_polling(drop_pending_updates=True)