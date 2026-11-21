# Panel local de mosAIc

Desde el entorno virtual Python 3.11 o posterior:

```bash
python -m pip install -e '.[api]'
mosaic key
python -m uvicorn isde.api:create_app --factory --host 127.0.0.1 --port 8001
```

Abre http://127.0.0.1:8001/. En vez del llavero también puedes configurar `ANTHROPIC_API_KEY` en el entorno. Las llamadas para analizar fuentes o respuestas usan Claude y pueden tener costo. El panel es para una integración de confianza en un proceso local: no lo publiques en red sin autenticación y control de acceso.

## Agentes

En «Agentes y prompts» elige y edita la instrucción del constructor de ontología, analista adversarial, analista de respuestas o reformulador de catálogo. Puedes restaurar los textos iniciales. El modelo Claude se selecciona de forma global con `MOSAIC_CLAUDE_MODEL`; los prompts son independientes por agente. La política se entrega como contexto a los agentes de análisis. El reformulador de `/v1/messages` recibe una alternativa de catálogo que el motor ya encontró admisible. En la revisión de texto libre, un agente externo o el operador aporta la reformulación.

## Secretos y vocabulario

En «Secretos y acceso» selecciona o crea una conclusión protegida: su identificador, descripción, estados posibles y umbrales. Marca los roles con permiso directo para recibirla; los demás roles pueden heredar permisos de sus roles superiores. Los identificadores y estados deben usar minúsculas, números o guion bajo, y comenzar con una letra. El panel no permite eliminar conclusiones ni quitar estados protegidos existentes.

Los conceptos ordinarios para hechos y reglas intermedias se agregan en «Conceptos y relaciones». Las conclusiones protegidas van en «Secretos y acceso». El modelo solo puede proponer hechos y reglas expresables con el vocabulario y las familias de la política; no descubre automáticamente un vocabulario ilimitado ni crea permisos.

## Construir y probar

1. En «Conocimiento y fuentes», elige un identificador nuevo, fecha, texto y, opcionalmente, los destinatarios que recibieron esa fuente.
2. Pulsa «Analizar fuente» y revisa las propuestas, sus citas y estimaciones. «Incorporar propuestas» guarda **exactamente** lo mostrado, sin una segunda llamada a Claude. Si el contexto cambia entre ambos pasos, hay que volver a analizar.
3. En «Probar una respuesta», selecciona el destinatario y escribe una tarea y un borrador. «Evaluar respuesta» revisa si satisface la tarea y si podría revelar conclusiones protegidas.
4. Si pide reformulación, cambia el texto y vuelve a evaluarlo. Si se aprueba, «Confirmar entrega» vuelve a verificar el contexto y actualiza la bandeja local.

La tarea y el texto son libres, pero la extracción debe poder representarse con la política y los hechos deben estar respaldados en la ontología interna. Las reglas propuestas por el modelo se usan de manera conservadora en el cálculo de riesgo y no verifican por sí solas como verdaderas las respuestas. Las estimaciones no están calibradas. La validación de citas y sintaxis no demuestra que las inferencias de Claude sean correctas o completas.

Los cambios de prompts, secretos, vocabulario y permisos se guardan en SQLite, por organización y operación. Tras editar la configuración, las revisiones aprobadas anteriormente se deben evaluar de nuevo. La base se controla con `MOSAIC_API_DB` (por defecto `mosaic-api.sqlite`); una configuración ya guardada tiene prioridad sobre el YAML inicial para esa base. Los destinatarios y roles siguen viniendo del archivo de caso y política. La página no controla agentes externos ni envía correos reales.
