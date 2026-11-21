# Patrones de AIxCC aplicables a ISDE

Fuente: análisis de Atlantis (Team Atlanta, 1.º en AIxCC 2025) y RoboDuck (Theori, 3.º). Ambos encuentran y parchean vulnerabilidades en C/C++ y Java. De ellos solo nos sirven los patrones de control de agentes LLM. El fuzzing, los PoV, el triage SARIF y Kubernetes no aplican a ISDE.

Licencias: Atlantis es GPL-3.0 (con partes MIT y GPL-2) y RoboDuck es AGPL-3.0. Tomamos solo ideas. No se copia su código a este repo.

| Patrón | Origen | Estado en ISDE |
| --- | --- | --- |
| Verificación ejecutable antes de responder | Ambos: un parche solo vale si compila, el PoV deja de fallar y pasan los tests | Implementado. `Guard._decide` solo entrega cláusulas con hechos respaldados en el grafo interno que completan la tarea. Si nada pasa, devuelve `review`. `propose` e `interpret` rechazan salidas sin cita literal o con vocabulario desconocido |
| El primero que pasa la validación gana | Atlantis crs-patch | Implementado en secuencia. `_decide` recorre las alternativas en orden de utilidad y entrega la primera admisible |
| Cascada de lo barato a lo caro | RoboDuck: un clasificador barato filtra antes de usar un agente caro | Parcial. Usa Haiku por defecto y no escala a un modelo mayor |
| Modelos por agente en configuración, con respaldo | RoboDuck `models-final.toml` | Pendiente. Hoy solo existe `ISDE_CLAUDE_MODEL`, compartido por `interpret` y `propose` |
| Presupuesto por tarea y por acción | Atlantis: `max_budget` de LiteLLM; RoboDuck: `SPEND_LIMITS` | Pendiente. `_request` solo limita `max_tokens=1500`, `timeout=25` y `max_retries=1` |
| Llamadas reproducibles y costo por mensaje | RoboDuck: agentes serializados y visor de logs | Parcial. Las entregas guardan la traza de la decisión y son idempotentes por `request_id`. Las llamadas a Claude no se registran |

## Próximos pasos

1. Configurar el modelo por función (`interpret`, `propose`) con un modelo de respaldo, en YAML junto a la política o con variables de entorno.
2. Fijar un tope de gasto por comando a partir de `response.usage` y cortar con `ValueError` al superarlo.
3. Registrar cada llamada a Claude en SQLite (entrada, salida y uso de tokens) para reproducir un caso.
4. Escalar a un modelo mayor solo cuando el barato no devuelva una selección válida.

Toda salida de Claude sigue siendo una propuesta hasta que la valide el código. Ningún cambio debe permitir que un texto llegue a la bandeja sin pasar por `_decide`.
