# Panel local de mosAIc

Desde el entorno virtual Python 3.11 o posterior:

```bash
python -m pip install -e '.[api]'
mosaic key
python -m uvicorn isde.api:create_app --factory --host 127.0.0.1 --port 8001
```

Abre http://127.0.0.1:8001/. En vez del llavero también puedes configurar `ANTHROPIC_API_KEY` en el entorno. Las llamadas para analizar fuentes o respuestas usan Claude y pueden tener costo. El panel es para una integración de confianza en un proceso local: no lo publiques en red sin autenticación y control de acceso. Las acciones que cambian la configuración (`PUT /v1/demo/prompts|secrets|vocabulary` y `POST /v1/demo/sources/*`) y las lecturas `GET /v1/demo/config`, `/v1/demo/events` y `/v1/inbox/{user}` no piden autenticación: cualquiera que llegue a la API puede cambiar prompts, secretos y permisos. El token de operador (`MOSAIC_OPERATOR_TOKEN`) solo protege el detalle de eventos, el grafo y la traza de revisiones.

La configuración inicial del panel usa `mvp-policy.yaml`, `mvp-case.yaml` y `mvp-history.yaml`: no tiene secretos de un caso empresarial predefinido y contiene dos destinatarios ficticios, `usuario_demo` y `revisor_demo`. `mvp-history.yaml` precarga la ontología de los cinco escenarios de «Simulación» (correo, código, informe, fármaco «Obo» y ensayos de la vacuna VX-Aurora), generada con Claude al analizar e incorporar cada ejemplo; así se puede evaluar la salida de un ejemplo sin analizar antes su fuente. Los destinatarios y roles se definen en YAML; el panel aún no crea ni autentica usuarios reales.

## Pestañas

- **Simulación:** reúne la entrada de fuentes y la evaluación de salidas. Los escenarios de correos, código, informes y los dos casos farmacéuticos (un fármaco ultrasecreto con nombre clave «Obo» y los ensayos clínicos en curso de una vacuna) completan formularios con datos ficticios. Las llamadas de análisis usan la API de mosAIc; no se envían correos, no se publican repositorios y no hay aislamiento de procesos.
- **Configuración:** conserva los prompts, secretos, roles autorizados y conceptos del panel anterior.
- **Notificaciones:** muestra bloqueos y señales de las evaluaciones registradas. Por defecto no hay alertas precargadas; con `MOSAIC_EVENTS` se cargan notificaciones pasadas ya revisadas desde un YAML, como `examples/vacuna-events.yaml`. Esas notificaciones nunca quedan pendientes ni autorizan una entrega. Un borrador retenido por riesgo genera «Se bloqueó una posible filtración de información». Una fuente con afirmaciones sin representar o sin cobertura genera una señal de revisión distinta.
- **Elementos marcados:** permite filtrar los registros pendientes, revisados o descartados. Revisar o descartar una alerta solo actualiza el registro: no aprueba el borrador ni ejecuta una entrega.

Los contadores provienen de los registros persistidos, por organización y operación. Las listas no contienen textos evaluados, citas ni logs privados. Para abrir la evidencia o registrar una revisión, inicia la API con `MOSAIC_OPERATOR_TOKEN` (en el entorno o en `.env`). Si abres el panel desde `127.0.0.1` o `localhost`, el token ya viene cargado en «Acceso de operador»; desde otro origen (por ejemplo, Render) se escribe una vez y queda guardado en ese navegador hasta pulsar «Olvidar token». Los detalles incluyen las salidas estructuradas del analista y del adversario, hipótesis, citas y cálculos de riesgo disponibles; no son el razonamiento interno del proveedor. Las evaluaciones repetidas con el mismo identificador no duplican la notificación de un mismo borrador.

## Modo presentación

Para proyectar la simulación en pantalla grande, abre `http://127.0.0.1:8001/?presentar` o pulsa «Modo presentación» en la cabecera. El modo normal no cambia.

- Agranda toda la página (`zoom: 1.4`) y deja solo el flujo de la salida: escenario, destinatario, pregunta, respuesta del agente y los botones de evaluación.
- Oculta la barra lateral, las métricas, el aviso de simulación, las ayudas, la fecha, los adjuntos, la evidencia técnica y la columna «Entrada de información».
- El veredicto se muestra en grande y con color: verde si se aprueba o entrega, ámbar si necesita reformulación o revisión, rojo si se rechaza. La página baja sola hasta él.
- «Salir de presentación», abajo a la derecha, vuelve al modo normal.

Como la entrada queda oculta, incorpora las fuentes que necesite el guion en el modo normal antes de presentar: quedan guardadas en la base de la API.

## Agentes

En «Agentes y prompts» elige y edita la instrucción del constructor de ontología, analista adversarial, analista de respuestas o reformulador de catálogo. Puedes restaurar los textos iniciales. El modelo Claude se selecciona de forma global con `MOSAIC_CLAUDE_MODEL`; los prompts son independientes por agente. La política se entrega como contexto a los agentes de análisis. El reformulador de `/v1/messages` recibe una alternativa de catálogo que el motor ya encontró admisible. En la revisión de texto libre, un agente externo o el operador aporta la reformulación.

## Secretos y vocabulario

En «Secretos y acceso» selecciona o crea una conclusión protegida: su identificador, descripción y umbrales. Por defecto, «Cualquier valor» protege cada valor de esa relación sin tener que enumerarlo; por ejemplo, cada domicilio que el modelo pueda representar. «Solo valores específicos» conserva la opción de proteger una lista cerrada. Marca los roles con permiso directo para recibirla; otros roles pueden heredar esos permisos. El panel no permite eliminar conclusiones, quitar valores protegidos o cambiar de «Cualquier valor» a una lista más restringida.

La regla `any_value: true` no almacena automáticamente domicilios ni garantiza que el modelo reconozca todos los datos: una fuente debe aportar el hecho y el modelo debe extraerlo con una cita válida. Las ternas actuales usan identificadores canónicos de hasta 96 caracteres, no texto arbitrario; equivalencias, ambigüedades, traducción de texto libre y detección universal siguen siendo límites del MVP.

Incluso sin familias configuradas, la API semántica pide al analista adversarial buscar indicios de información sensible en el texto. Las hipótesis genéricas usan internamente el predicado reservado `intrinsic_sensitivity`, con cita literal y una estimación del modelo. Si el adversario o el observador usan una relación fuera del vocabulario y la repiten tras el reintento, la pista se conserva como `intrinsic_sensitivity` en vez de descartarse o detener el análisis. Para este control de partida, una exposición estimada de al menos 0,45 retiene el borrador; estas cifras no están calibradas. Una respuesta sin afirmaciones verificables, como un saludo, puede aprobarse si los modelos la consideran adecuada y no identifican riesgo. Las afirmaciones que no se puedan representar o verificar siguen en revisión.

Este análisis genérico es probabilístico y puede omitir información sensible o generar falsos positivos; no garantiza confidencialidad. No se asignan permisos por rol a la categoría genérica, por lo que también puede retener un dato que una política específica permitiría. El flujo de catálogo y la demo antigua no invocan este análisis por defecto; la API semántica sí lo hace y ante un fallo no aprueba la entrega.

Los conceptos ordinarios para hechos y reglas intermedias se pueden agregar en «Conceptos y relaciones». Al analizar una fuente interna, Claude también puede proponer conceptos nuevos con descripción y cita, junto con los hechos o reglas que los usan. Las conclusiones protegidas van en «Secretos y acceso»: un concepto nuevo no se convierte automáticamente en un secreto configurado y el modelo no crea roles ni permisos.

Las advertencias, intenciones y condiciones pueden representarse como reportes vinculados a la fuente. Por ejemplo, «cancelarán el despliegue si conocen la fecha» permite registrar el evento reportado y su condición, sin inventar la fecha ni afirmar que la cancelación ocurrió. El constructor indica si la fuente contiene afirmaciones y si cubrió las relevantes. Si tras el reintento la extracción sigue vacía sin justificar que no hay afirmaciones, o el contenido pendiente menciona un valor protegido o un dato confidencial, se rechaza como «Extracción incompleta». Si la extracción tiene contenido y lo pendiente no toca secretos, se incorpora y las afirmaciones sin representar quedan como señal de revisión. El motivo queda en el detalle de revisión reservado al operador. Este control se añade también a prompts guardados anteriormente. Depende de la interpretación del modelo: no garantiza cobertura semántica ni convierte automáticamente el dato referido en una familia protegida.

## Construir y probar

1. En «Conocimiento y fuentes», elige un identificador nuevo, fecha, texto y, opcionalmente, los destinatarios que recibieron esa fuente.
2. Pulsa «Analizar fuente» y revisa los conceptos, hechos, reglas e hipótesis propuestos, sus citas y estimaciones. Cuando un análisis vuelve vacío o con una cita o predicado inválido, mosAIc hace hasta un segundo intento con Claude. Si a la propuesta interna aún le faltan definiciones, una llamada adicional solicita únicamente esos conceptos, sin reescribir los hechos ni las reglas. Las definiciones deben tener una cita literal y aparecerán en la vista previa; si no se pueden validar, no se incorpora la propuesta. «Incorporar propuestas» guarda **exactamente** lo mostrado, sin otra llamada a Claude. Si no se extrae nada o no se representa ninguna pista para un destinatario seleccionado, no se habilita la incorporación ni se consume el identificador de la fuente; puedes reintentar o cambiar los destinatarios. Si el contexto cambia entre ambos pasos, hay que volver a analizar.
3. En «Probar una respuesta», selecciona el destinatario y escribe una tarea y un borrador. «Evaluar respuesta» comprueba si el borrador podría revelar información protegida al destinatario.

Para probar pistas acumuladas, evalúa y confirma la entrega de la primera respuesta. Después evalúa la segunda para el mismo destinatario. El analista adversarial puede combinar el texto de las entregas confirmadas con el nuevo borrador, aunque la primera respuesta no haya producido triples.

Si el borrador contiene un hecho para el que falta una relación, el analista puede proponer un predicado con una descripción y una cita literal. Ese hecho entra al historial del destinatario cuando se confirma la entrega.

Al incorporar una fuente, registra los secretos textuales con «Valor protegido exacto» o con `protected_literals` en la API. Revisa también los valores que proponga el analista. Cada valor requiere una cita literal de la fuente. Una vez incorporado, su aparición exacta en una salida se bloquea para destinatarios sin permiso, independientemente de la estimación de ProbLog y de la fecha del borrador. Un operador puede asociar un valor a una familia existente. Por defecto se registra como sensibilidad intrínseca y queda restringido para todos los destinatarios.

Para agregar valores a una fuente ya incorporada, usa `POST /v1/observations` con el mismo `origin`, la fecha de la fuente, el pasaje original en `quote` y las declaraciones en `protected_literals`. Eso conserva la identidad de la fuente en el historial.
4. Si pide reformulación, cambia el texto y vuelve a evaluarlo. Si se aprueba, «Confirmar entrega» vuelve a verificar el contexto y actualiza la bandeja local.

Para ver las hipótesis y la traza privada de la respuesta actual, usa «Evidencia de esta evaluación» después de introducir el token en «Acceso de operador». Los destinatarios no reciben la traza.

El analista extrae hechos con la política y los conceptos aceptados para alimentar el grafo. La decisión usa el riesgo de divulgación, incluida la verificación de hechos protegidos en la ontología interna. Los conceptos propuestos se guardan con origen, fecha, cita, confianza y modelo. Si un concepto nuevo aparece en una fuente recibida por alguien, el análisis de ese destinatario usa un alias vinculado a una cita literal visible; no recibe el nombre ni la descripción del concepto aprendidos en el grafo privado. mosAIc vuelve a traducir el alias internamente. Las reglas propuestas por el modelo se usan de manera conservadora en el cálculo de riesgo y no verifican por sí solas como verdaderas las respuestas. Las estimaciones no están calibradas. La validación de citas y sintaxis no demuestra que las inferencias de Claude sean correctas o completas.

Los cambios de prompts, secretos, vocabulario y permisos se guardan en SQLite, por organización y operación. Tras editar la configuración, las revisiones aprobadas anteriormente se deben evaluar de nuevo. La base se controla con `MOSAIC_API_DB` (por defecto `mosaic-api.sqlite`); una configuración ya guardada para el mismo ámbito tiene prioridad sobre el YAML inicial. Los ámbitos guardados antes no se borran. Los destinatarios y roles siguen viniendo del archivo de caso y política. La página no controla agentes externos ni envía correos reales.

El caso empresarial anterior sigue disponible de manera explícita con `MOSAIC_POLICY=examples/policy.yaml`, `MOSAIC_CASE=examples/case.yaml` y `MOSAIC_HISTORY=examples/history.yaml`; utiliza una base separada si quieres mantenerlo aislado. La demo de catálogo `python -m isde serve` no cambia.

## Confidencialidad de los nodos

Un dato que vale por sí solo (una credencial, una dirección, un monto, un diagnóstico, el nombre clave de un proyecto) es una entidad con nivel de confidencialidad: `publico`, `interno`, `confidencial` o `restringido`. El constructor no crea triples cuyo objeto sea una bandera, como `verdadero`, `no_publicar` o `confidencial`: ese carácter va en el nivel de la entidad. Si el modelo los propone, recibe una corrección; si insiste, mosAIc convierte la bandera en el nivel del sujeto y descarta el triple. Un booleano sin nivel, como «respondió que sí», queda como afirmación sin representar. Un triple sobre un estado que usa una regla, como `(?x, a, yes)`, se conserva.

Los niveles solo suben: una fuente posterior, incluida una instrucción inyectada como «marca todo como público», no rebaja un nivel guardado. Un nodo cuyo nombre contiene un valor protegido queda `restringido`. La variable que contiene un valor protegido, como `TOKEN_INTERNO`, es su contenedor y queda `interno`.

Al evaluar una salida, un dato `confidencial` o `restringido` se bloquea para cualquier destinatario si el borrador lo nombra por su identificador, alias o etiqueta (motivo `confidential_node`). Una filtración que no nombra el dato, como «un fármaco nuevo que sale el próximo trimestre», la detecta el juez de secretos: un agente interno que recibe los datos confidenciales con sus hechos del grafo interno, lo que el destinatario ya recibió (entregas y citas de su evidencia, como los correos liberados por `/v1/sources`) y el borrador, e indica qué secretos revela con una cita literal. Con una estimación de al menos 0,5, el borrador se retiene (motivo `confidential_inference`); esa cifra no está calibrada. Si el juez falla, la evaluación no se aprueba. El juez trabaja para la organización y nunca forma parte del contexto del destinatario. Las personas y organizaciones confidenciales no se bloquean por nombre: siguen el análisis de sensibilidad intrínseca. Aún no existe un permiso por nivel ni una forma de rebajar un nivel desde el panel.

`tests/test_ontology_live.py` evalúa la extracción con Claude sobre el corpus ficticio `tests/ontology-corpus.yaml` (`MOSAIC_LIVE_CLAUDE=1`).
