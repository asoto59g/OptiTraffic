# OptiTraffic

Es una app / simulador de tráfico vehicular para analizar mejoras en **demarcación horizontal**, **vertical**, **semáforos**, **parqueo**, **descarga de mercancía**, etc., en una ciudad.

## Fuente de mapa

La fuente del mapa para extraer información es [Geofabrik](https://download.geofabrik.de/).

## Flujo de uso

1. Al ingresar a la app lo primero que solicita es la **ciudad** y el **país**.
2. Es recomendable crear un **polígono** (forma libre o rectangular) o subir un **GeoJSON** en lat/lon de la zona a analizar.
3. Una vez definido el polígono de trabajo se solicitan varios parámetros de configuración.

## Configuración de red

### Semáforos

- Ubicación de semáforos.
- Por defecto se asumen tiempos de espera: **rojo 45 s**, **amarillo 5 s**, **verde 45 s**.
- Configurable al gusto del usuario.

### Carriles y sentidos

- En zonas de una sola vía, por defecto se indica que solo un vehículo cabe en el ancho de calzada.
- Posterior se pueden cambiar vías específicas a **dos vías en un solo sentido**.

### Parqueo

- Definir tramos de cuadrante con opción de parqueo vehicular.
- Dejar **distancias legales** en las esquinas.
- Indicar de qué lado de la calzada se permite parqueo: un lado o ambos.
- Para ambos lados, la calle debe ser lo suficientemente ancha.

### Señales de alto

- Agregar señales de alto en las esquinas donde corresponda.

## Simulación de flujo

Una vez configurados los parámetros de flujo vehicular de la ciudad, se carga **densidad de vehículos** para todas las calles para ver el comportamiento de **presas vehiculares**.

Donde hay altos en las esquinas se espera que los conductores den paso de cortesía y respeten no bloquear el cruce por alta densidad.
