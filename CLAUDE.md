# OpenBench - Lab Instrument Orchestration Platform

## Visión del proyecto
OpenBench es una plataforma unificada para orquestar instrumentos de laboratorio 
(osciloscopios, generadores de funciones, fuentes DC, lock-in amplifiers, 
analizadores de impedancia) a través de:
- Una API Python coherente
- Una GUI moderna estilo 2026
- Experimentos reutilizables predefinidos

## Principio arquitectónico fundamental
OpenBench es una **capa de composición**, NO una reimplementación.

Importa backends existentes maduros como librerías:
- `backends/virtualbench_backend.py` → wraps `vbarrido-py` (existente, ~/virtualBench-NI)
- `backends/sr860_backend.py` → wraps `sr860-impedance-workbench` (existente, ~/sr860-impedance-workbench)
- `backends/keysight_backend.py` → wraps `keysight_E36312A_DCSweep` (existente, ~/keysight_E36312A_DCSweep)
- `backends/rigol_backend.py` → wraps `rigol_ds1000e_python` (existente, ~/rigol_ds1000e_python)
- `backends/tektronix_backend.py` → wraps `tektronix-tbs1000c-linux` (existente, ~/tektronix-tbs1000c-linux)

Cada backend implementa una o más interfaces abstractas de `core/interfaces.py`.

## Módulo SOFIA (filter design) - INTEGRADO COMO CORE
SOFIA está integrado como módulo principal `openbench.filters`. Permite:
- Diseñar filtros activos (Sallen-Key, MFB, etc.) 
- Generar el circuito de prueba automáticamente
- Validar respuesta teórica vs medida usando los backends de OpenBench

Flujo de integración:
1. Usuario diseña filtro en `openbench.filters` (SOFIA)
2. OpenBench genera setup de medición automáticamente
3. VirtualBench/SR860 mide respuesta real
4. Compara teoría vs medición, exporta reporte

## GUI Stack
**CustomTkinter** para look moderno 2026 con dark/light theme.
Razones:
- Migración fácil desde código Tkinter existente en backends
- Look profesional sin curva de aprendizaje de Qt
- Cross-platform sin dependencias pesadas

## Deadline crítico
**LUNES**: Sesión de laboratorio universidad. Necesario:
- `examples/chua_lab_workflow.py` funcionando
- Ejecuta 3 mediciones automatizadas:
  1. Caracterización TC4069UBP (Keysight DC + VirtualBench scope)
  2. Caracterización de bobinas (SR860 frequency sweep)
  3. Barrido de admitancia Chua (SR860 + Keysight bias)

## Estructura del proyecto
openbench/
├── core/
│   ├── interfaces.py      # IInstrument, IOscilloscope, IFunctionGenerator, etc.
│   ├── orchestrator.py    # Discovery, coordinación
│   ├── experiment.py      # Base class para experimentos
│   └── session.py         # Manejo de sesiones de medición
├── backends/
│   ├── virtualbench_backend.py
│   ├── sr860_backend.py
│   ├── keysight_backend.py
│   ├── rigol_backend.py
│   └── tektronix_backend.py
├── experiments/
│   ├── dc_sweep.py
│   ├── frequency_sweep.py
│   ├── impedance_sweep.py
│   ├── chua_admittance.py    # Específico Chua
│   └── component_char.py
├── filters/                   # SOFIA integrado
│   ├── design.py
│   ├── topologies.py
│   └── validation.py
├── gui/
│   ├── main.py
│   ├── theme.py
│   ├── widgets/
│   └── panels/
├── utils/
│   ├── scpi_helpers.py
│   └── data_export.py
└── data/
├── recorder.py
└── plotter.py
## Convenciones de código
- Type hints obligatorios en métodos públicos
- Docstrings estilo Google
- Tests con pytest en `tests/`
- Logging vía `logging` stdlib, NO print()
- Configuraciones via YAML, NO hardcoded

## Restricciones importantes
1. **NO reimplementar funcionalidad existente.** Si vbarrido-py ya hace algo, importarlo.
2. **Mantener compatibilidad.** Los repos backend deben seguir funcionando standalone.
3. **Modo simulación obligatorio.** Todo experimento debe correr sin hardware para desarrollo.
4. **GUI moderna.** Look 2026, dark/light theme, no Tkinter clásico visible.

## Gestión de tareas
Tareas en Taskwarrior con `project:openbench`. Ver con:
```bash
task project:openbench
```

Orquestador:
```bash
~/bin/openbench-orchestrator.sh next   # Siguiente tarea
~/bin/openbench-orchestrator.sh auto   # Sprint completo
~/bin/openbench-orchestrator.sh status # Status del proyecto
```
