# mosAIc

<img src="./project-logo.png" alt="Logo de mosAIc" width="200" />

**Guardrail que evita que un agente de IA revele un secreto al combinarse con pistas previas.**

Dos datos inofensivos por separado pueden, juntos, delatar información sensible. mosAIc revisa la respuesta de un agente antes de entregarla, estima con ProbLog cuánto subiría la probabilidad de que el destinatario deduzca una conclusión protegida y, si el riesgo es alto, entrega una versión que igual cumple la tarea o la deja en revisión.

## En vivo

| | URL |
|---|---|
| Panel y API de mosAIc | https://mosaic-7uf1.onrender.com |
| Intranet de AlphaCorp (caso de prueba que usa la API) | https://alphacorp-intranet.onrender.com |

Ambas corren en el plan gratuito de Render: la primera visita puede tardar cerca de un minuto mientras el servicio despierta.

## Cómo decide

1. **Grafo interno (RDF/OWL):** relaciones de la empresa, roles vigentes por fecha y permisos por operación.
2. **Vista del observador:** lo que el destinatario ya pudo saber por su historial.
3. **Claude** extrae del borrador hechos con cita literal; el código valida cada uno contra el vocabulario de la política.
4. **ProbLog** combina las pistas y compara la probabilidad de deducir el secreto con y sin la respuesta.
5. **`Guard`** decide: `allow`, `repair` (reformula y vuelve a evaluar) o `review`. Nada llega a la bandeja sin pasar por esa decisión.

Detalle completo en [docs/isde.md](docs/isde.md).

## Correr en local

Requiere Python 3.11 o posterior.

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[api]'
python -m isde demo                         # caso ficticio en memoria, sin clave de Claude
python -m unittest discover -s tests -v     # tests sin credenciales; Claude se simula
```

Panel con Claude:

```bash
cp .env.example .env    # agrega ANTHROPIC_API_KEY (o usa `mosaic key`)
python -m uvicorn isde.api:create_app --factory --host 127.0.0.1 --port 8001
```

Abre http://127.0.0.1:8001/ (o `/?presentar` para pantalla grande). El panel permite editar prompts y secretos, analizar fuentes y evaluar respuestas de texto libre. Los análisis con Claude consumen API. Detalles y límites en [docs/panel.md](docs/panel.md).

Para la intranet de AlphaCorp, inicia la API con el caso clásico:

```bash
MOSAIC_POLICY=examples/policy.yaml MOSAIC_CASE=examples/case.yaml MOSAIC_HISTORY=examples/history.yaml \
  python -m uvicorn isde.api:create_app --factory --port 8001
```

- `POST /v1/messages` solo entrega texto del catálogo del caso. El perfil por defecto (`examples/mvp-*.yaml`) no tiene tareas y responde 400 «Tarea desconocida».
- `POST /v1/reviews` evalúa texto libre (`task: "email"` o `"freeform"`), con adjuntos opcionales.
- Las acciones posibles son `allow`, `repair` y `review`; no existe `block`.

`python -m isde serve` muestra la demo original, con respuestas de catálogo y sin Claude, en http://127.0.0.1:8000/.

## Estructura

```text
.
├── isde/                       # paquete Python (CLI `isde` / `mosaic`)
│   ├── model.py                # contratos Pydantic: Policy, Case, Observation, Batch
│   ├── graphs.py               # grafo OWL, vistas interna y del observador, caminos y ProbLog
│   ├── guard.py                # Guard: registra evidencia, decide y entrega (SQLite)
│   ├── privacy.py              # identificadores personales chilenos (RUT) como confidenciales
│   ├── claude.py               # interpret, propose y juez de secretos con herramienta forzada
│   ├── prompts.py              # prompts iniciales de los agentes
│   ├── media.py                # adjuntos: imágenes/PDF con Claude, audio con ElevenLabs
│   ├── api.py                  # app FastAPI
│   ├── workflow.py             # /v1/messages: alternativas de catálogo y reformulaciones
│   ├── semantic.py             # análisis de texto e inferencia por destinatario
│   ├── reviews.py              # /v1/reviews: vista previa, revisión y entrega
│   ├── events.py               # registro de alertas para el operador
│   ├── dashboard.py            # panel web (HTML embebido)
│   ├── serve.py                # demo visual sin dependencias
│   └── cli.py                  # comandos: demo, serve, key, load, interpret, propose, preview, deliver, inbox
├── examples/                   # políticas, casos, historiales y fuentes ficticias (los usan los tests)
├── tests/                      # unittest; test_guard.py cubre el comportamiento principal
├── docs/                       # documentación en español
├── testbeds/alphacorp-intranet # submódulo: intranet ficticia en Node que llama a la API
└── .github/workflows/          # actualiza el submódulo de la intranet cada 5 minutos
```

El submódulo se descarga con `git submodule update --init`. Su repositorio es [jpherrerap/alphacorp-intranet](https://github.com/jpherrerap/alphacorp-intranet).

## Documentación

- [docs/isde.md](docs/isde.md): cómo decide, política en YAML, historial persistente, integración con Claude y límites conocidos.
- [docs/panel.md](docs/panel.md): configuración de agentes, secretos y pruebas desde el panel.
- [docs/inferencias-multinivel.md](docs/inferencias-multinivel.md): reglas y pruebas de inferencias encadenadas.
- [docs/demo.md](docs/demo.md): guion de 3 minutos para presentar la demo.
- [project-description.md](project-description.md): ficha del proyecto.
- [AGENTS.md](AGENTS.md): guía para agentes de código (invariantes y convenciones).

## Equipo

Platanus Hack 26: Santiago ft. Buk · team-24 · Track 🛡️ AI Security

- Claudio Alexis Canales Pavez ([@claudiocanalespavez](https://github.com/claudiocanalespavez))
- Gabriel Joaquín Carmona Díaz ([@m1tuw](https://github.com/m1tuw))
- Juan Pablo Herrera Pizarro ([@jpherrerap](https://github.com/jpherrerap))
- Diego Agustín Moreno Guzmán ([@diegomorenoxor](https://github.com/diegomorenoxor))
- Sebastián Nicolás Ferrá Sepúlveda ([@sebastianferra](https://github.com/sebastianferra))
