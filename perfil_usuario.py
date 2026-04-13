"""
perfil_usuario.py — Gestión de perfiles persistentes de usuarios
=================================================================
Guarda los perfiles en data/perfiles.json (se crea automáticamente).
Cada perfil tiene:
  - nombre, edad, ocupacion, gustos
  - historial de conversación (últimos 10 turnos)
  - emociones_frecuentes: conteo por tipo
  - ultima_emocion, total_mensajes, primer_mensaje
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("Charlie.Perfiles")

RUTA_DATA = Path("data")
RUTA_JSON = RUTA_DATA / "perfiles.json"


class PerfilUsuario:
    def __init__(self):
        RUTA_DATA.mkdir(exist_ok=True)
        self._cache: dict = self._cargar()

    def _cargar(self) -> dict:
        if RUTA_JSON.exists():
            try:
                with open(RUTA_JSON, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("perfiles.json corrupto — se reinicia.")
        return {}

    def _guardar(self):
        with open(RUTA_JSON, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def obtener(self, user_id: int) -> dict:
        """Devuelve el perfil del usuario (o uno vacío si no existe)."""
        return self._cache.get(str(user_id), {})

    def actualizar(self, user_id: int, datos: dict):
        """
        Actualiza campos del perfil con los datos proporcionados.
        Hace merge superficial: los campos existentes no mencionados
        en `datos` se conservan.
        """
        uid = str(user_id)
        perfil_actual = self._cache.get(uid, {})
        perfil_actual.update(datos)
        self._cache[uid] = perfil_actual
        self._guardar()

    def eliminar(self, user_id: int):
        """Borra el perfil completo del usuario."""
        uid = str(user_id)
        if uid in self._cache:
            del self._cache[uid]
            self._guardar()