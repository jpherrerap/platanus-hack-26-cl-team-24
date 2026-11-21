# team-24 Platanus Hack 26: Santiago ft. Buk Project

<img src="./project-logo.png" alt="Logo de mosAIc" width="200" />

Track: 🛡️ AI Security

team-24

- Claudio Alexis Canales Pavez ([@claudiocanalespavez](https://github.com/claudiocanalespavez))
- Gabriel Joaquín Carmona Díaz ([@m1tuw](https://github.com/m1tuw))
- Juan Pablo Herrera Pizarro ([@jpherrerap](https://github.com/jpherrerap))
- Diego Agustín Moreno Guzmán ([@diegomorenoxor](https://github.com/diegomorenoxor))
- Sebastián Nicolás Ferrá Sepúlveda ([@sebastianferra](https://github.com/sebastianferra))

## mosAIc

mosAIc es un guardrail que revisa la respuesta de un agente de IA antes de entregarla. Detecta cuándo una frase inocente, sumada a lo que el destinatario ya sabe, le permitiría deducir un secreto, y en ese caso entrega una versión que igual cumple la tarea o la deja en revisión. La API evalúa borradores libres mediante extracción con Claude, una vista adversarial por destinatario y reglas verificables. La demo visual funciona con un catálogo de ejemplos.

## Probarlo

Requiere Python 3.11 o posterior. Desde la raíz del repositorio, en macOS o Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m isde demo
python -m unittest discover -s tests
```

La demo usa datos ficticios, corre en memoria y no necesita clave de Claude. La API de texto libre requiere una clave y el extra de instalación `api`. `python -m isde serve` muestra el mismo caso como página local en http://127.0.0.1:8000/.

## Documentación

- [docs/isde.md](docs/isde.md): cómo decide, política en YAML, historial persistente, integración con Claude y límites conocidos.
- [docs/demo.md](docs/demo.md): guion de 3 minutos para presentar la demo.
- [docs/inferencias-multinivel.md](docs/inferencias-multinivel.md): reglas y pruebas de inferencias encadenadas.
- [project-description.md](project-description.md): ficha del proyecto.
