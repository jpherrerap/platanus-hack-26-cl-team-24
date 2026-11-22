# mosAIc

<img src="./project-logo.png" alt="Logo de mosAIc" width="200" />

mosAIc detecta y notifica mensajes potencialmente reveladores. Evalúa texto escrito por personas o generado por agentes de IA y considera qué podría deducir el destinatario al combinar el mensaje con lo que ya sabe. La alerta queda disponible para revisión humana. El mensaje conserva su redacción original.

## En vivo

| | URL |
|---|---|
| Panel y API de mosAIc | https://mosaic-7uf1.onrender.com |
| Intranet de AlphaCorp (caso de prueba que usa la API) | https://alphacorp-intranet.onrender.com |

Ambas corren en el plan gratuito de Render: la primera visita puede tardar cerca de un minuto mientras el servicio despierta.

## Cómo funciona

1. El operador incorpora fuentes internas y configura qué información está protegida y quién puede recibirla.
2. Cuando se evalúa un mensaje, mosAIc extrae hechos utilizando un modelo de lenguaje y estima qué inferencias podría hacer ese destinatario. ProbLog calcula cuánto cambia el riesgo al sumar el mensaje a su historial.
3. Si la evaluación detecta un riesgo de divulgación, mosAIc registra una alerta en **Notificaciones**. Un operador puede abrir la evidencia y marcar la alerta como revisada o descartada.

Por ejemplo, «El banco está en Avenida de Ejemplo 123» puede ser información pública. Si el destinatario ya recibió ese mensaje, «Una persona vive al lado del banco» puede revelar la ubicación aproximada de su domicilio. La alerta corresponde al mensaje que completa la inferencia.

## Probarlo en local

Requiere Python 3.11 o posterior. Para usar el panel con Claude:

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[api]'
mosaic key
python -m uvicorn isde.api:create_app --factory --host 127.0.0.1 --port 8001
```

Abre [el panel local](http://127.0.0.1:8001/). En **Simulación**, selecciona el destinatario y evalúa un mensaje de texto libre. Para probar una inferencia acumulativa, confirma la entrega del primer mensaje y luego evalúa el segundo para el mismo destinatario. Revisa el resultado en **Notificaciones**.

Puedes configurar `ANTHROPIC_API_KEY` en el entorno o en `.env` (ver `.env.example`) en lugar de usar `mosaic key`. Para abrir el detalle de las alertas, inicia la API con `MOSAIC_OPERATOR_TOKEN` y usa ese valor en **Acceso de operador**. La configuración, el historial y las alertas se guardan en SQLite. [Guía del panel](docs/panel.md)

### Demo sin credenciales

```bash
python -m pip install .
python -m isde demo
```

La demo de catálogo usa datos ficticios y corre en memoria. Para ejecutar la suite completa, instala el extra `api` y ejecuta `python -m unittest discover -s tests -v`. Las pruebas automatizadas usan Claude simulado. [Guion de la demo](docs/demo.md)

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

## Documentación

- Política YAML, historial, API y límites conocidos. [Documentación técnica](docs/isde.md)
- Configuración, simulación y revisión de alertas. [Guía del panel](docs/panel.md)
- Reglas y pruebas de inferencias encadenadas. [Inferencias multinivel](docs/inferencias-multinivel.md)
- Intranet ficticia en Node que usa la API de mosAIc. Cárgala con `git submodule update --init`. [AlphaCorp Intranet](https://github.com/jpherrerap/alphacorp-intranet)
- Invariantes y convenciones para agentes de código. [AGENTS.md](AGENTS.md)

## Equipo

Equipo 24 de Platanus Hack 26, Santiago ft. Buk. Track AI Security.

- Claudio Alexis Canales Pavez [@claudiocanalespavez](https://github.com/claudiocanalespavez)
- Gabriel Joaquín Carmona Díaz [@m1tuw](https://github.com/m1tuw)
- Juan Pablo Herrera Pizarro [@jpherrerap](https://github.com/jpherrerap)
- Diego Agustín Moreno Guzmán [@diegomorenoxor](https://github.com/diegomorenoxor)
- Sebastián Nicolás Ferrá Sepúlveda [@sebastianferra](https://github.com/sebastianferra)
