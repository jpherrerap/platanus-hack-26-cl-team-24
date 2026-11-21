# ISDE

ISDE revisa una respuesta antes de entregarla a un destinatario. La primera versión ofrece una API Python y una CLI para una bandeja local. El ejemplo es ficticio.

## Ejecutar desde la raíz del repositorio

Requiere Python 3.11 o posterior.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[claude]'
isde demo
python -m unittest discover -s tests -v
```

La demo funciona sin credenciales. Para usar un historial persistente:

```bash
isde load examples/history.yaml
isde preview luis due --draft "El trámite vence el viernes. El expediente O-17 corresponde a BetaAI."
isde deliver luis due solicitud_001
isde inbox luis
```

La salida de `preview` y `deliver` es una traza privada para el operador. El destinatario recibe exclusivamente el texto de la bandeja. En el ejemplo Luis recibe «El trámite vence el viernes». La bandeja, el historial y la selección de la respuesta se confirman en una sola transacción de SQLite. Repetir el identificador de una entrega devuelve su resultado original. La CLI es una herramienta local de confianza. Una integración debe suministrar la identidad autenticada, la organización y la operación, y publicar únicamente desde la bandeja. El envío a servicios externos requiere su propio adaptador.

## Política y conocimiento

[examples/policy.yaml](../examples/policy.yaml) configura roles, herencia, permisos para consultar recursos y recibir conclusiones, vocabulario, estados protegidos y umbrales. [examples/case.yaml](../examples/case.yaml) define el ámbito, el directorio de asignaciones, las cláusulas permitidas y las alternativas en orden de utilidad. Se pueden sustituir con `isde --policy ruta --case ruta --db ruta.sqlite …`. Los identificadores de rol y de origen proceden de la integración de confianza.

RDFLib construye una vista interna y una vista de evidencia por destinatario a partir de eventos en SQLite. OWL 2 RL deriva la jerarquía de roles y las cadenas de propiedades. A partir de la misma cadena configurable, ProbLog calcula la probabilidad de que el observador pueda reunir las premisas. El evaluador consulta afirmaciones y negaciones explícitas. La falta de evidencia da soporte cero a esa vía y deja abierto el estado real. Una hipótesis externa plausible también puede motivar una reparación si la empresa carece de verificación interna. La traza indica si el grafo interno respalda la conclusión.

| Evidencia sobre el conocimiento | Valor de partida |
| --- | ---: |
| El rol permitía consultar la fuente en la fecha de exposición | 0,15 |
| Hipótesis sobre información externa al directorio | Hasta 0,50 |
| Mensaje entregado | 0,75 |
| Lectura registrada | 0,90 |
| La persona citó la pista | 0,98 |

Los valores son supuestos editables. Una estimación de Claude se limita al máximo del grado de evidencia. El motor recalcula históricos con los pesos vigentes, manteniendo intactos los registros de entrega. La revocación de un rol elimina su autorización desde el fin de vigencia y conserva las exposiciones anteriores. Para reproducir una evaluación histórica se necesita la versión de política y directorio de aquel momento.

Para la familia del ejemplo, el guardrail protege tanto la confirmación como el descarte. Cuando falta autorización, una revelación directa se retiene incluso si se estima que el destinatario ya conoce el secreto. Una inferencia indirecta se retiene si aumenta el conocimiento y el resultado alcanza 0,75 o el aumento alcanza 0,45. Se escoge la primera alternativa admisible que cumple la tarea. Si faltan hechos de respaldo o alternativas útiles, la decisión es `review`.

El modelo de probabilidad supone independencia entre orígenes distintos. Las pistas y copias de un mismo origen comparten un evento aleatorio. El adaptador debe conservar el identificador del origen subyacente y distinguir piezas que se leen por separado. El número estima la capacidad de deducción conforme al modelo. Su calibración requiere casos etiquetados de destinatarios reales o simulados. La vista del piloto está acotada a un destinatario, organización y operación. El historial cruza sesiones de ese ámbito.

## Añadir Claude

```bash
isde key
isde load examples/history.yaml
isde interpret examples/vendor-card.yaml --grade possible_access
isde propose examples/internal-source.yaml > propuestas.json
# Tras revisar las citas, los IDs y los triples propuestos:
isde load propuestas.json
```

`isde key` usa entrada oculta y el llavero del sistema. También se admite `ANTHROPIC_API_KEY`. El modelo se elige con `ISDE_CLAUDE_MODEL`. Las llamadas envían a Anthropic la política, el texto de la fuente y la vista correspondiente. Para una tarjeta externa, se incluye únicamente el conocimiento atribuido al destinatario y sus roles aplicables.

`interpret` utiliza una tarjeta con fragmentos previamente vinculados a triples por el adaptador. Con `propose`, Claude puede descubrir entidades y relaciones en texto nuevo usando el vocabulario activo. Una propuesta conserva la cita literal para revisar su significado antes de incorporarla. Para sugerir conocimientos externos se suministra una fuente del observador con `user` y `--grade external_hint`. Las reglas y las autorizaciones se cambian en YAML.

## Integrar un agente

```python
from datetime import date
from isde.guard import Guard
from isde.model import Case, Policy

guard = Guard("isde.sqlite", Policy.load("examples/policy.yaml"), Case.load("examples/case.yaml"))
try:
    borrador = "El trámite vence el viernes. El expediente O-17 corresponde a BetaAI."
    decision = guard.run("luis", "due", date.today(), request_id="solicitud_002", draft=borrador)
    # El consumidor autorizado lee su bandeja tras la confirmación de la entrega.
finally:
    guard.close()
```

Esta versión entrega respuestas compuestas de cláusulas previamente asociadas a hechos y requisitos de tarea. Un borrador que coincide con una alternativa se evalúa primero. Para texto libre desconocido, el guardrail busca la respuesta de catálogo más corta que complete la tarea y pase la evaluación. El catálogo y su correspondencia entre texto y triples son entradas de confianza. El análisis semántico de borradores arbitrarios, los grupos de destinatarios y la correlación entre distintas operaciones quedan para próximas versiones.
