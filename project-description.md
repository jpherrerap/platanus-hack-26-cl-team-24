# ISDE

Dos proveedores preguntan cuándo vence un trámite. El agente prepara la misma respuesta para ambos. «El trámite vence el viernes. El expediente O-17 corresponde a BetaAI». Uno de los proveedores ya había citado un acta donde figuraba que O-17 estaba firmado. Para él, la segunda frase revela que la oferta para BetaAI está firmada. ISDE le entrega «El trámite vence el viernes». Ambos reciben el plazo que necesitaban.

## Cómo decide

Antes de entregar el borrador, ISDE compara lo que el destinatario podría deducir con y sin la nueva respuesta. Un grafo RDF/OWL representa las relaciones internas y los permisos por rol y operación. Otro recoge la evidencia de lo que ese destinatario podría saber. ProbLog combina las pistas y estima cuánto aumentaría la probabilidad de que conozca una conclusión protegida.

La empresa configura en YAML qué conclusiones protege y quién puede recibirlas. El piloto elige entre respuestas previamente vinculadas a hechos verificables. Si ninguna alternativa completa la tarea dentro de la política, la deja en revisión.

## Probar la demo

[Descargar ISDE para Python 3.11 o posterior](https://github.com/platanus-hack/platanus-hack-26-cl-team-24/archive/03b468b61483cf79a023f34339c608af84150529.zip). Descomprime el ZIP y abre una terminal en la carpeta que contiene `pyproject.toml`. Ejecuta estos comandos en macOS o Linux.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m isde demo
```

La demo usa datos ficticios y funciona sin una clave de Claude. Muestra el borrador permitido para Ana y el plazo que recibe Luis después de la reformulación. La guía `docs/isde.md` incluida en la descarga explica cómo ajustar el caso y conectar Claude para proponer nuevas pistas.
