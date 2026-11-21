# mosAIc

Guardrail que revisa la respuesta de un agente antes de entregarla. Estima con ProbLog cuánto subiría la probabilidad de que el destinatario deduzca una conclusión protegida, combinando un grafo RDF/OWL interno con la evidencia de lo que el destinatario ya pudo saber. Descripción completa en `docs/isde.md`.

## Comandos

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[claude]'
python -m unittest discover -s tests -v   # sin credenciales; Claude se simula
isde demo                                 # caso ficticio en memoria
```

## Mapa

- `isde/model.py`: contratos Pydantic (`Policy`, `Case`, `Observation`, `Batch`). `extra="forbid"`. Los IDs siguen `^[a-z][a-z0-9_]{0,95}$`.
- `isde/graphs.py`: esquema OWL, roles vigentes por fecha, vista interna, vista del observador y probabilidades con ProbLog.
- `isde/guard.py`: `Guard` con SQLite. `observe` registra evidencia, `_decide` elige la alternativa y `run` confirma la entrega en una sola transacción. `inbox` es lo único que ve el destinatario.
- `isde/claude.py`: `interpret` y `propose`. Claude responde con una herramienta forzada y el código valida cada resultado.
- `isde/media.py`: adjuntos de `/v1/messages`. Imágenes y PDF se transcriben con Claude y audio con ElevenLabs Scribe (`ELEVENLABS_API_KEY`, `ISDE_STT_MODEL`). Un mensaje con adjuntos nunca se reescribe: si no es admisible, va a `review`.
- `isde/serve.py`: demo visual local (stdlib `http.server`, solo vista previa).
- `isde/cli.py`: CLI `isde` (`demo`, `serve`, `key`, `load`, `interpret`, `propose`, `preview`, `deliver`, `inbox`).
- `examples/*.yaml`: política, caso, historial y fuentes de ejemplo (ficticios). Los tests los usan.

## Invariantes

- Nada llega a la bandeja sin pasar por `Guard._decide`. Si ninguna alternativa es admisible, la decisión es `review`.
- La salida de Claude es una propuesta. Cada triple exige una cita literal de la fuente y predicados y estados del vocabulario de la política.
- El contexto de un observador nunca incluye el grafo interno, las asignaciones ni otros destinatarios (`Guard.model_context`).
- Las entregas son idempotentes por `request_id` y respetan el orden del historial. Los registros de entrega no se reescriben.
- Rol, organización, operación y origen vienen de la integración de confianza, nunca del modelo.
- La API key se lee de `ANTHROPIC_API_KEY` o del llavero (`isde key`); la de ElevenLabs, de `ELEVENLABS_API_KEY`. Nunca van en archivos del repo.

## Convenciones

- Código compacto, sin capas extra. Los comentarios explican solo el porqué, en inglés. Los mensajes al usuario y la documentación van en español.
- Los errores de entrada se señalan con `ValueError`; la CLI los convierte en código de salida 2.
- Todo cambio de comportamiento lleva su test en `tests/test_guard.py`. Usa el cliente simulado de `ClaudeTests` en vez de la API real.
- Modelo por defecto: `claude-haiku-4-5-20251001`, reemplazable con `ISDE_CLAUDE_MODEL`.
- Mensajes de commit en español, al estilo de `Implementa el guardrail ISDE con RDF/OWL y ProbLog`.
