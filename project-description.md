# mosAIc

mosAIc ayuda a las empresas a evitar que sus agentes de IA revelen información confidencial de forma indirecta. Antes de responder, evalúa qué podría deducir cada destinatario a partir de la respuesta, su rol y la información previa a la que pudo haber tenido acceso.

## Cómo decide

Antes de entregar el borrador, mosAIc compara lo que el destinatario podría deducir con y sin la nueva respuesta. Un grafo RDF/OWL representa las relaciones internas y los permisos por rol y operación. Otro recoge la evidencia de lo que ese destinatario podría saber. ProbLog combina las pistas y estima cuánto aumentaría la probabilidad de que conozca una conclusión protegida.

La empresa configura en YAML qué conclusiones protege y quién puede recibirlas. La API analiza borradores libres con Claude y comprueba los hechos y las hipótesis contra la política. Cuando detecta riesgo, pide al agente una nueva versión y vuelve a evaluarla.

El análisis de texto depende de la extracción de Claude. Los pesos son supuestos sin calibrar y la demo usa una bandeja local.

## Probar la demo

[Descargar mosAIc para Python 3.11 o posterior](https://github.com/platanus-hack/platanus-hack-26-cl-team-24/archive/refs/heads/main.zip). Descomprime el ZIP y abre una terminal en la carpeta que contiene `pyproject.toml`. Ejecuta estos comandos en macOS o Linux.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m isde demo
```

La demo usa datos ficticios y funciona sin una clave de Claude. Muestra el borrador permitido para Ana y el plazo que recibe Luis después de la reformulación. La guía `docs/isde.md` incluida en la descarga explica cómo ajustar el caso y conectar Claude para proponer nuevas pistas.
