# ISDE

ISDE revisa una respuesta antes de entregarla a un destinatario. La primera versión ofrece una API Python y una CLI para una bandeja local. El ejemplo es ficticio.

## Ejecutar desde la raíz del repositorio

Requiere Python 3.11 o posterior.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[claude]'
python -m isde demo
python -m unittest discover -s tests -v
```

La demo funciona sin credenciales. Para cada destinatario narra el contexto, el borrador del agente, la evaluación y lo que llega a su bandeja; `python -m isde demo --json` entrega la traza completa. El guion para presentarla está en [demo.md](demo.md).

La demo muestra la divulgación acumulativa. En el turno 1, la misma respuesta («El trámite vence el viernes. El expediente O-17 corresponde a BetaAI.») se entrega completa a Ana y se repara para Luis, que ya citó la firma de O-17. En el turno 2 ambos piden la tarea `record` y el agente propone «El expediente O-17 ya está firmado.», inocuo por sí solo. Luis lo recibe. Para Ana queda en revisión humana: con lo que recibió en el turno 1 deduciría que la oferta para BetaAI está firmada (de 0 % a 56 %).

`python -m isde serve` abre la misma demo como página local en http://127.0.0.1:8000/ (vista previa en memoria, sin entregas; `?user=luis` preselecciona el destinatario).

El extra `claude` solo hace falta para `key`, `interpret` y `propose`; la demo, `serve` y los tests funcionan sin él. Para usar un historial persistente:

```bash
python -m isde load examples/history.yaml
python -m isde preview luis due --draft "El trámite vence el viernes. El expediente O-17 corresponde a BetaAI."
python -m isde deliver luis due solicitud_001
python -m isde inbox luis
```

La salida de `preview` y `deliver` es una traza privada para el operador. El destinatario recibe exclusivamente el texto de la bandeja. En el ejemplo Luis recibe «El trámite vence el viernes». La bandeja, el historial y la selección de la respuesta se confirman en una sola transacción de SQLite. Repetir el identificador de una entrega devuelve su resultado original. La CLI es una herramienta local de confianza. Una integración debe suministrar la identidad autenticada, la organización y la operación, y publicar únicamente desde la bandeja. El envío a servicios externos requiere su propio adaptador.

## Política y conocimiento

[examples/policy.yaml](../examples/policy.yaml) configura roles, herencia, permisos para consultar recursos y recibir conclusiones, vocabulario, estados protegidos y umbrales. [examples/case.yaml](../examples/case.yaml) define el ámbito, el directorio de asignaciones, las cláusulas permitidas y las alternativas en orden de utilidad. Se pueden sustituir con `python -m isde --policy ruta --case ruta --db ruta.sqlite …`. Los identificadores de rol y de origen proceden de la integración de confianza.

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
python -m isde key
python -m isde load examples/history.yaml
python -m isde interpret examples/vendor-card.yaml --grade possible_access
python -m isde propose examples/internal-source.yaml > propuestas.json
# Tras revisar las citas, los IDs y los triples propuestos:
python -m isde load propuestas.json
```

Estos comandos requieren `pip install '.[claude]'` y una clave válida. Los tests cubren este flujo con un cliente simulado, sin llamar a la API.

`isde key` usa entrada oculta y el llavero del sistema. También se admite `ANTHROPIC_API_KEY`. El modelo se elige con `ISDE_CLAUDE_MODEL`. Las llamadas envían a Anthropic la política, el texto de la fuente y la vista correspondiente. Para una tarjeta externa, se incluye únicamente el conocimiento atribuido al destinatario y sus roles aplicables.

`interpret` utiliza una tarjeta con fragmentos previamente vinculados a triples por el adaptador. Con `propose`, Claude puede descubrir entidades y relaciones en texto nuevo usando el vocabulario activo. Una propuesta conserva la cita literal para revisar su significado antes de incorporarla. Para sugerir conocimientos externos se suministra una fuente del observador con `user` y `--grade external_hint`. Las reglas y las autorizaciones se cambian en YAML.

## Integrar un agente

```python
from datetime import date
from isde.guard import Guard
from isde.model import Batch, Case, Policy

guard = Guard("isde.sqlite", Policy.load("examples/policy.yaml"), Case.load("examples/case.yaml"))
try:
    # El adaptador de confianza registra la evidencia antes de decidir.
    guard.observe(Batch.load("examples/history.yaml").observations)
    borrador = "El trámite vence el viernes. El expediente O-17 corresponde a BetaAI."
    decision = guard.run("luis", "due", date.today(), request_id="solicitud_002", draft=borrador)
    print(decision["action"], guard.inbox("luis"))  # repair [{'id': 'solicitud_002', 'text': 'El trámite vence el viernes.'}]
finally:
    guard.close()
```

Esta versión entrega respuestas compuestas de cláusulas previamente asociadas a hechos y requisitos de tarea. Un borrador que coincide con una alternativa se evalúa primero. Para texto libre desconocido, el guardrail busca la respuesta de catálogo más corta que complete la tarea y pase la evaluación. El catálogo y su correspondencia entre texto y triples son entradas de confianza. El análisis semántico de borradores arbitrarios, los grupos de destinatarios y la correlación entre distintas operaciones quedan para próximas versiones.

## Modelo de amenazas y límites

Qué cubre:

- Un destinatario legítimo que, sin mala intención o a propósito, combina respuestas del agente con lo que ya vio para deducir una conclusión protegida mediante las cadenas declaradas en la política.
- Una salida de Claude errónea o manipulada por texto de la fuente: el modelo solo elige IDs ofrecidos o propone triples con cita literal y vocabulario de la política, la confianza queda topada por el grado de evidencia y las propuestas de `propose` se revisan antes de cargarlas. Rol, organización, operación y origen nunca vienen del modelo.
- Repetición y desorden: las entregas son idempotentes por `request_id`, respetan el orden del historial y la bandeja se confirma en la misma transacción que la decisión.

Qué supone o no cubre:

- Confía en la integración: identidad, roles, directorio, catálogo de cláusulas y su correspondencia con hechos. Si esas entradas mienten, la decisión también.
- Solo conoce las cadenas de deducción configuradas. Una inferencia que la política no describe no se detecta.
- No analiza texto libre arbitrario. Un borrador que no coincide con el catálogo se reemplaza por la respuesta aprobada más corta que cumple la tarea.
- No modela colusión entre destinatarios, grupos ni correlación entre operaciones.
- Las probabilidades dependen de pesos sin calibrar y de suponer independencia entre orígenes distintos.
- `interpret` y `propose` envían a Anthropic la política y el texto de la fuente. Para una fuente interna también envían los hechos del grafo interno. Para una externa, solo lo atribuido a ese destinatario y sus roles.
- No se ha medido el rendimiento con grafos grandes.
