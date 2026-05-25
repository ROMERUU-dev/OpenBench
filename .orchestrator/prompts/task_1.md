# Tarea 1 - OpenBench

## Contexto del proyecto
Lee primero `CLAUDE.md` en la raíz del proyecto para entender la arquitectura completa.

OpenBench es una plataforma de orquestación de instrumentos de laboratorio que:
- Importa backends existentes (vbarrido-py, sr860-impedance-workbench, etc.)
- Provee GUI moderna 2026 con CustomTkinter
- Integra SOFIA para diseño de filtros
- Está orientada al deadline crítico del lunes (lab session)

## Tarea específica
**Implementar DataRecorder con CSV + JSON metadata**

## Fase actual
`6-data`

## Requisitos
1. Implementa la tarea completamente y profesionalmente
2. Type hints obligatorios en métodos públicos
3. Docstrings estilo Google en clases y funciones
4. Logging vía `logging` stdlib (NO print)
5. Sigue la estructura de carpetas en CLAUDE.md
6. Mantén compatibilidad con backends existentes
7. Si creas algo nuevo, agrega test mínimo en `tests/`

## Validación al terminar
```bash
# Debe importar sin errores
python3 -c "import openbench"

# Tests deben pasar
pytest tests/ -x --tb=short
```

## Commit
Cuando termines, haz commit con formato:
```
[6-data] Implementar DataRecorder con CSV + JSON metadata

- Detalle de archivo 1 creado/modificado
- Detalle de archivo 2 creado/modificado

Refs: task #1
```

## Reporta al final
- Archivos creados/modificados
- Decisiones de diseño relevantes
- Qué sugieres para próxima tarea
- Si encontraste algo que requiere atención del usuario
