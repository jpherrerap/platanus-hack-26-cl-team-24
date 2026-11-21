# ISDE

ISDE ayuda a las empresas a evitar que sus agentes de IA revelen información confidencial de forma indirecta. Antes de responder, evalúa qué podría deducir cada destinatario a partir de la respuesta, su rol y la información previa a la que pudo haber tenido acceso.

## Cómo decide

Antes de entregar el borrador, ISDE compara lo que el destinatario podría deducir con y sin la nueva respuesta. Un grafo RDF/OWL representa las relaciones internas y los permisos por rol y operación. Otro recoge la evidencia de lo que ese destinatario podría saber. ProbLog combina las pistas y estima cuánto aumentaría la probabilidad de que conozca una conclusión protegida.

La empresa configura en YAML qué conclusiones protege y quién puede recibirlas. El piloto elige entre respuestas previamente vinculadas a hechos verificables. Si ninguna alternativa completa la tarea dentro de la política, la deja en revisión.

Límites de esta versión: las respuestas salen de un catálogo de cláusulas vinculadas a hechos, no de texto libre; los pesos de evidencia son supuestos sin calibrar; y no cubre colusión entre destinatarios.

## Probar la demo

[Descargar ISDE para Python 3.11 o posterior](https://github.com/platanus-hack/platanus-hack-26-cl-team-24/archive/refs/heads/main.zip). Descomprime el ZIP y abre una terminal en la carpeta que contiene `pyproject.toml`. Ejecuta estos comandos en macOS o Linux.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m isde demo
```

La demo usa datos ficticios y funciona sin una clave de Claude. Muestra el borrador permitido para Ana y el plazo que recibe Luis después de la reformulación. La guía `docs/isde.md` incluida en la descarga explica cómo ajustar el caso y conectar Claude para proponer nuevas pistas.
