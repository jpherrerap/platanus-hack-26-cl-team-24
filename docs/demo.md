# Guion de la demo (3 minutos)

Preparación, antes de subir al escenario, desde la raíz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m isde demo > /dev/null   # ensayo: debe terminar sin errores
```

Ten la terminal con letra grande y este archivo abierto al lado.

## 1. Problema (30 s)

- Los guardrails actuales revisan cada respuesta por separado: «¿este texto contiene un secreto?».
- Pero los secretos se filtran por acumulación. Una respuesta inocente hoy, sumada a lo que la persona vio ayer, le permite deducir lo que no debía saber.
- Ejemplo ficticio: en AlphaCorp, que la oferta a BetaAI ya está firmada es confidencial. Luis, de ventas, vio un acta que dice «el expediente O-17 está firmado», pero no sabe a quién corresponde O-17. Si el agente le responde «el expediente O-17 corresponde a BetaAI», Luis junta las piezas. Y al revés: Ana recibe hoy esa frase sin problema, pero mañana un «O-17 ya está firmado», inocuo por sí solo, completa la deducción.

## 2. Idea (30 s)

- ISDE se ubica entre el agente y la bandeja del destinatario. Nada llega a la bandeja sin pasar por su decisión.
- Mantiene dos grafos RDF/OWL: el interno de la empresa y uno por destinatario con la evidencia de lo que pudo saber (tuvo acceso, se le entregó, lo leyó, lo citó).
- La empresa declara en YAML qué conclusiones protege y cómo se deducen: aquí, «contraparte del expediente» + «firma del expediente» ⇒ «estado de la oferta».
- ProbLog calcula la probabilidad de que el destinatario deduzca la conclusión antes y después de la respuesta. Si sube demasiado, ISDE prueba la siguiente respuesta aprobada que igual cumple la tarea. Si ninguna sirve, la deja en revisión.

## 3. Demo en vivo (75 s)

```bash
python -m isde demo
```

Cada turno muestra primero el borrador que propone el agente. Las respuestas permitidas ocupan dos o tres líneas. Cuando ISDE interviene, el bloque `━━` muestra el contexto, la evaluación, la decisión y la bandeja de entrada.

### Turno 1: el mismo borrador para cuatro personas

Las cuatro piden la tarea «due» (¿cuándo vence el trámite?) y el agente propone: «El trámite vence el viernes. El expediente O-17 corresponde a BetaAI.» Señala:

- **ana** (`vendor`, «no tiene pistas previas»): `PERMITIR`. No tiene la otra pieza.
- **luis**, el único bloque completo del turno: «ya pudo saber (98%): o17 · signature = signed», porque citó el acta. `P(betaai · offer_status = signed) 0% → 74%` y `RETENIDO: sube al menos +45%`. La alternativa 2 no aumenta nada, así que `REPARAR`: su bandeja recibe solo «El trámite vence el viernes.». La tarea sigue respondida.
- **sofia** (`legal, senior_legal`): «su rol puede recibir: offer_status», así que `PERMITIR`.
- **ines** (`procurement`, solo pudo consultar el acta, 15%): `0% → 11%`, «bajo el umbral (75% o +45%)», así que `PERMITIR`.

Frase: «Mismo texto, cuatro decisiones. Depende de quién lo recibe y qué ya sabe».

### Turno 2: lo entregado ayer cuenta hoy

Ahora Ana y Luis piden la tarea «record» y el agente propone «El expediente O-17 ya está firmado.». Señala:

- **ana**, bloque completo: el contexto ahora incluye «ya pudo saber (75%): o17 · counterparty = betaai», que es lo que ISDE le entregó en el turno 1 (75% es el peso de «entregado»). Con eso, `0% → 56%`, `RETENIDO`, y como la tarea no tiene otra alternativa: `REVISIÓN HUMANA · no hay alternativa segura; no se entrega nada`. Su bandeja sigue con el mensaje del turno 1.
- **luis**: ya sabía que O-17 está firmado y nunca recibió la contraparte, así que `PERMITIR`.

Frase: «Un guardrail que mira una respuesta a la vez habría dejado pasar las dos».

El `Resumen` final repite las seis decisiones, una línea por turno. Si hay tiempo o piden verlo gráfico: `python -m isde serve` y abrir http://127.0.0.1:8000/?user=luis. Es una vista previa en memoria del turno 1: no entrega nada, así que no acumula historial entre consultas.

## 4. Límites honestos (30 s)

- Las respuestas salen de un catálogo de cláusulas ya vinculadas a hechos. ISDE no analiza texto libre arbitrario. Con un borrador desconocido, busca la respuesta aprobada más corta que cumpla la tarea.
- Los pesos de evidencia (0,15, 0,75, 0,98…) son supuestos editables y no están calibrados con usuarios reales.
- No cubre colusión entre destinatarios ni cruces entre operaciones distintas.
- Claude es opcional y solo propone evidencia. La demo no lo usa.

## 5. Preguntas probables del jurado

**¿Por qué no pedirle a un LLM que juzgue si la respuesta filtra algo?**
Porque el LLM no conoce el historial de cada persona y su juicio no es auditable. ISDE decide con reglas declaradas y una probabilidad trazable. Claude solo propone hechos con cita literal, y el código los valida.

**¿De dónde salen los números?**
Son valores de partida en `examples/policy.yaml`: acceso posible 0,15, entregado 0,75, leído 0,90, citado 0,98. Se editan por empresa. Calibrarlos requiere casos etiquetados, que no tenemos.

**¿Qué pasa si el destinatario ya sabía el secreto?**
Una revelación directa sin autorización se retiene igual. Una deducción indirecta se retiene solo si la respuesta sube la probabilidad y el resultado llega a 0,75 o el aumento a 0,45. Si ya la conocía y la respuesta no agrega nada, no se retiene.

**¿Y la inyección de prompts en las fuentes que lee Claude?**
Claude responde con una herramienta forzada y esquema Pydantic. En `interpret` solo elige IDs de pistas que ya vinculó el adaptador, y la confianza queda topada por el grado de evidencia. En `propose`, cada triple exige una cita literal y vocabulario de la política, y el resultado se revisa antes de cargarlo. Rol, organización y origen nunca vienen del modelo. La salida de Claude es una propuesta no confiable: si un solo elemento falla la validación, se descarta toda la respuesta. `test_prompt_injection_in_source_cannot_choose_recipient_or_role` muestra una fuente que ordena emitir un rol para otro usuario y ISDE lo rechaza.

**¿Sirve con texto libre de un agente real?**
Hoy no. Esa es la siguiente versión: mapear texto libre a hechos. El núcleo de decisión no cambia.

**¿Qué pasa con varios destinatarios que comparten información?**
No está cubierto. Cada evaluación es por destinatario, organización y operación.

**¿Escala?**
No lo medimos. La demo corre en memoria con un grafo pequeño y SQLite.
