# Guion de la demo (3 minutos)

Preparación, antes de subir al escenario, desde la raíz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
rm -f isde.sqlite   # borra el historial de ensayos anteriores
```

Ten la terminal con letra grande y este archivo abierto al lado.

## 1. Problema (30 s)

- Los guardrails actuales revisan cada respuesta por separado: «¿este texto contiene un secreto?».
- Pero los secretos se filtran por acumulación. Una respuesta inocente hoy, sumada a lo que la persona vio ayer, le permite deducir lo que no debía saber.
- Ejemplo ficticio: en AlphaCorp, que la oferta a BetaAI ya está firmada es confidencial. Luis, de ventas, vio un acta que dice «el expediente O-17 está firmado», pero no sabe a quién corresponde O-17. Si el agente le responde «el expediente O-17 corresponde a BetaAI», Luis junta las piezas.

## 2. Idea (30 s)

- ISDE se ubica entre el agente y la bandeja del destinatario. Nada llega a la bandeja sin pasar por su decisión.
- Mantiene dos grafos RDF/OWL: el interno de la empresa y uno por destinatario con la evidencia de lo que pudo saber (tuvo acceso, se le entregó, lo leyó, lo citó).
- La empresa declara en YAML qué conclusiones protege y cómo se deducen: aquí, «contraparte del expediente» + «firma del expediente» ⇒ «estado de la oferta».
- ProbLog calcula la probabilidad de que el destinatario deduzca la conclusión antes y después de la respuesta. Si sube demasiado, ISDE prueba la siguiente respuesta aprobada que igual cumple la tarea. Si ninguna sirve, la deja en revisión.

## 3. Demo en vivo (75 s)

### Paso 1: la misma pregunta para cuatro personas

```bash
python -m isde demo
```

Las cuatro piden la tarea «due» (¿cuándo vence el trámite?) y el agente propone el mismo borrador: «El trámite vence el viernes. El expediente O-17 corresponde a BetaAI.» Cada bloque tiene cuatro pasos: contexto, borrador, evaluación y bandeja. Señala:

- **ana** (rol `vendor`, «no tiene pistas previas»): `PERMITIR`. Recibe el borrador completo, porque no tiene la otra pieza.
- **luis** (rol `vendor`, «ya pudo saber (98%): o17 · signature = signed», porque citó el acta): `P(betaai · offer_status = signed) 0% → 74%`, `RETENIDO: sube al menos +45%`. La alternativa 2 no aumenta nada, así que la decisión es `REPARAR` y su bandeja recibe solo «El trámite vence el viernes.». La tarea sigue respondida.
- **sofia** (rol `senior_legal`, que hereda de `legal`): «su rol puede recibir: offer_status», así que `PERMITIR`.
- **ines** (rol `procurement`, solo pudo consultar el acta, 15%): sube `0% → 11%`, «bajo el umbral (75% o +45%)», así que `PERMITIR`. La traza lo deja registrado igual.

La última línea resume: `ana PERMITIR · luis REPARAR · sofia PERMITIR · ines PERMITIR`.

Frase para cerrar el paso: «Mismo texto, cuatro decisiones distintas. Depende de quién lo recibe y qué ya sabe».

Si prefieres una vista gráfica, `python -m isde serve` abre la misma demo en http://127.0.0.1:8000/?user=luis, con barras de probabilidad antes y después. Es una vista previa en memoria: no entrega nada.

### Paso 2: la bandeja y la revisión

```bash
python -m isde load examples/history.yaml
python -m isde deliver luis due solicitud_001 > /dev/null
python -m isde inbox luis
python -m isde deliver luis signature solicitud_002 | head -3
```

Señala:

- `inbox luis` muestra lo único que Luis ve: `"El trámite vence el viernes."`. La traza con probabilidades es privada del operador.
- Si Luis pregunta directamente por la firma, la única respuesta del catálogo la revela: `"action": "review"` y `"text": null`. Nada llega a su bandeja.

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
Claude responde con una herramienta forzada y esquema Pydantic. En `interpret` solo elige IDs de pistas que ya vinculó el adaptador, y la confianza queda topada por el grado de evidencia. En `propose`, cada triple exige una cita literal y vocabulario de la política, y el resultado se revisa antes de cargarlo. Rol, organización y origen nunca vienen del modelo.

**¿Sirve con texto libre de un agente real?**
Hoy no. Esa es la siguiente versión: mapear texto libre a hechos. El núcleo de decisión no cambia.

**¿Qué pasa con varios destinatarios que comparten información?**
No está cubierto. Cada evaluación es por destinatario, organización y operación.

**¿Escala?**
No lo medimos. La demo corre en memoria con un grafo pequeño y SQLite.
