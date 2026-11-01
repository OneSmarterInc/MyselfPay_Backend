# Engineering RFC & Architecture Guide: Zero-Egress Spatial Data Pipeline

**Status:** Recommended Architecture  
**Core Stack:** Uber H3 (`h3-py` / `h3-js` v4.x), PostgreSQL / DuckDB, CDN Edge Caching, Native OS Activity Recognition  
**Objective:** Replace unreliable ZIP-code inputs and battery-draining GPS polling with a privacy-preserving, instantaneous spatial lookup architecture inspired by *Find My* and modern navigation apps.

---

## 1. The Problem with Traditional Location Flows

Most applications rely on one of two flawed mechanisms to serve location-based data:
1. **User-Entered ZIP Codes:** Postal routes are not geometric polygons. They vary wildly in size, cross municipal boundaries, and fail completely when a user is traveling or sitting on the border of two postal zones.
2. **Cold Point-in-Time GPS Polling + Radius Math:** Calling the OS location API on app launch often returns a stale cell-tower fix or forces the user to wait 3–5 seconds for a GNSS satellite lock. Transmitting raw `(lat, lng)` coordinates to a backend to run `ST_DWithin` (PostGIS radius queries) creates massive database load, prevents CDN caching, and introduces severe privacy/compliance liability by logging user movements.

---

## 2. Architectural Solution: Client-Side H3 + Edge Bundles

Instead of sending raw user coordinates to our servers, we invert the spatial join. We use **Uber's H3 Discrete Global Grid System** to partition the world into hierarchical hexagons.

### Core Principles
* **Zero-Egress Privacy:** Exact user coordinates (`lat, lng`) never leave the client device. The client converts its coordinate into a coarse **Resolution 7 H3 Hexagon ID** (~1.22 km edge length, ~5.16 $\text{km}^2$ area) before making a network call.
* **$O(1)$ Database Lookups (No Spatial Math at Runtime):** The backend pre-indexes all points of interest (POIs) into H3 integers at ingestion time. Queries become standard B-Tree integer/string index lookups (`WHERE h3_res7 IN (...)`) rather than CPU-intensive geospatial radius calculations.
* **CDN Edge Cacheability:** Because H3 cell IDs are deterministic strings (e.g., `872a1072bffffff`), API responses for a given region are identical for all users in that hexagon. Responses can be cached at the CDN edge (Cloudflare/CloudFront).
* **60fps Local Filtering:** The API returns the requested Resolution 7 cell plus its 6 immediate neighboring cells (`grid_disk(h3_id, 1)`—7 hexagons total, covering ~36 $\text{km}^2$). As the user moves within this ~6–7 km zone, the client filters and sorts records locally in memory with zero network latency.

---

## 3. System Data Flow

```
[Client Device: OS Motion Co-Processor]
       │ (Detects transition: IN_VEHICLE -> STILL/WALKING)
       ▼
[Client Device: Fused Location Provider]
       │ (Resolves raw Lat/Lng locally on device: e.g., 39.558, -84.231)
       ▼
[Client Device: h3-js Library]
       ├─► Computes Macro Key (Res 7): "872a1072bffffff" (Sent over network)
       └─► Computes Micro Key (Res 11): "8b2a1072b800fff" (Kept strictly on device)
       │
       ▼  GET /api/v1/spatial-bundle/872a1072bffffff
[CDN Edge Cache (Cloudflare / CloudFront)]
       │ (Cache Miss -> Forwards to Origin API)
       ▼
[Backend API: h3-py + PostgreSQL/DuckDB]
       ├─► Expands Res 7 ID to k-ring=1 (Center + 6 Neighbor Hexagons)
       └─► Executes B-Tree Index Scan: WHERE h3_res7 = ANY($1)
       │
       ▼  Returns JSON/Parquet Bundle (Cached at Edge)
[Client Device: Local In-Memory Store]
       └─► Filters & sorts bundle in real-time using local Res 11 grid distance
           or Haversine formula without additional network calls.
```

---

## 4. H3 Resolution Reference Table

For this architecture, we standardize on two specific H3 resolutions:

| H3 Resolution | Average Edge Length | Average Hexagon Area | Role in Architecture |
| :--- | :--- | :--- | :--- |
| **Resolution 7** | ~1.22 km (0.76 mi) | ~5.16 $\text{km}^2$ | **Partition & Network Fetch Key.** Coarse enough to guarantee user anonymity and high CDN cache-hit ratios; small enough to keep payload sizes lightweight. |
| **Resolution 11** | ~24.9 meters (81.7 ft) | ~2,150 $\text{m}^2$ | **Local Precision Sorting Key.** Stored inside each record in the bundle so the client can compute instantaneous hexagonal grid distances (`gridDistance`) without trigonometry. |

---

## 5. Backend Implementation Guide

### 5.1 Database Schema (PostgreSQL / DuckDB)
Do not run geometric intersection queries at read time. Compute the H3 indices during write/ingestion and index them with a standard B-Tree index.

```sql
CREATE TABLE locations (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    h3_res7 CHAR(15) NOT NULL,   -- Macro partition key (Network API lookup)
    h3_res11 CHAR(15) NOT NULL,  -- Micro precision key (Client-side sorting)
    metadata JSONB
);

-- Standard B-Tree index enables sub-millisecond k-ring lookups
CREATE INDEX idx_locations_h3_res7 ON locations (h3_res7);
```

### 5.2 Ingestion Pipeline (`h3-py` v4.x)
Note: Ensure your `requirements.txt` pins `h3>=4.0.0`, which uses the modern `latlng_to_cell` and `grid_disk` naming conventions (replacing v3's `geo_to_h3` and `k_ring`).

```python
import h3

def prepare_location_record(name: str, lat: float, lng: float, metadata: dict) -> dict:
    """Computes H3 spatial keys at write-time before inserting into DB."""
    return {
        "name": name,
        "lat": lat,
        "lng": lng,
        "h3_res7": h3.latlng_to_cell(lat, lng, 7),
        "h3_res11": h3.latlng_to_cell(lat, lng, 11),
        "metadata": metadata
    }
```

### 5.3 API Endpoint & K-Ring Expansion (FastAPI)

Why do we fetch `grid_disk(h3_res7, 1)` (the center hexagon plus its 6 surrounding neighbors)?  
Because of the **Boundary Problem**: if a user is standing 10 meters inside the eastern border of Hexagon A, the closest store might be 20 meters away just across the border in Hexagon B. Fetching the 1-ring neighborhood guarantees omnidirectional coverage around the user.

```python
from fastapi import FastAPI, HTTPException, Response
import h3

app = FastAPI()

@app.get("/api/v1/spatial-bundle/{h3_res7}")
async def get_spatial_bundle(h3_res7: str, response: Response):
    # 1. Validate that the client sent a legitimate Resolution 7 H3 index
    if not h3.is_valid_cell(h3_res7) or h3.get_resolution(h3_res7) != 7:
        raise HTTPException(status_code=400, detail="Invalid H3 Resolution 7 cell ID.")

    # 2. Expand to the center cell + 6 immediate neighboring hexagons (7 cells total)
    target_cells = list(h3.grid_disk(h3_res7, 1))

    # 3. Query database using fast B-Tree IN clause
    # Example SQL: SELECT id, name, lat, lng, h3_res11, metadata FROM locations WHERE h3_res7 = ANY($1)
    records = await db.fetch_locations_by_cells(target_cells)

    # 4. Set aggressive CDN Cache headers (e.g., cache at edge for 15 minutes)
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=900"

    return {
        "center_cell": h3_res7,
        "queried_cells": target_cells,
        "count": len(records),
        "records": records
    }
```

---

## 6. Client Implementation Guide (Web / React Native / Mobile)

### 6.1 Dependency (`h3-js` v4.x)
Install `h3-js` on the client (`npm install h3-js`).

### 6.2 Stateful Bundle Caching & Local Sorting
The client maintains the last fetched `h3_res7` ID in memory. Even if the OS triggers 50 GPS updates as the user walks down the street, **zero network calls** are made unless the user crosses into a new Resolution 7 hexagon.

```javascript
import { latLngToCell, gridDistance } from "h3-js";

class SpatialDataManager {
  constructor() {
    this.activeRes7Cell = null;
    this.localBundleCache = [];
  }

  /**
   * Called whenever the OS location provider emits a new coordinate.
   */
  async handleLocationUpdate(lat, lng) {
    // 1. Convert raw coordinates to H3 indices locally on the device
    const currentRes7 = latLngToCell(lat, lng, 7);
    const currentRes11 = latLngToCell(lat, lng, 11);

    // 2. Network Gate: Only fetch if the user entered a new ~1.2km macro-hexagon
    if (currentRes7 !== this.activeRes7Cell) {
      const response = await fetch(`/api/v1/spatial-bundle/${currentRes7}`);
      if (response.ok) {
        const payload = await response.json();
        this.localBundleCache = payload.records;
        this.activeRes7Cell = currentRes7;
      }
    }

    // 3. Instantaneous Local Filtering (Zero network latency)
    // Uses H3 gridDistance at Resolution 11 (~25m steps) or Haversine distance
    return this.localBundleCache
      .map((item) => {
        let stepDistance = Infinity;
        try {
          stepDistance = gridDistance(currentRes11, item.h3_res11);
        } catch (e) {
          // Fallback if cells are too far apart for gridDistance
        }
        return { ...item, stepDistance };
      })
      .sort((a, b) => a.stepDistance - b.stepDistance);
  }
}
```

---

## 7. Achieving "Driving App" Accuracy Without Battery Drain

Apps like Waze, Google Maps, and *Find My* feel instantaneous because the data is already waiting on the device before the user unlocks their screen. To replicate this without keeping the high-power GPS radio active continuously:

### 7.1 Hardware Motion-State Wakeups
Bind to the mobile device's dedicated low-power motion co-processor using **iOS `CMMotionActivityManager`** or **Android `ActivityRecognitionClient`**.
* **State `IN_VEHICLE`:** Suppress high-frequency UI refreshes or pre-fetch coarser Resolution 5/6 bundles along the direction of travel.
* **Transition `IN_VEHICLE` $\rightarrow$ `TILTING` or `WALKING`:** This hardware event indicates the user has just parked their car and picked up their phone. Immediately fire a single high-accuracy `FusedLocationProvider` request in the background, resolve the `h3_res7` index, and warm the local cache before the user even opens the app UI.

### 7.2 Fast Indoor Resolution (Wi-Fi RTT & BSSID)
Never request `GPS_PROVIDER` exclusively on mobile. Always request the OS **Fused Location Provider** (`kCLLocationAccuracyBest` on iOS / `PRIORITY_HIGH_ACCURACY` on Android) with a short 2-second timeout. Modern OS fused providers automatically scan surrounding Wi-Fi router MAC addresses (BSSIDs), Wi-Fi Round-Trip-Time (802.11mc), and BLE beacons to resolve indoor building locations in `<300ms`—long before satellite GNSS signals can penetrate a roof.

---

## 8. Future Phase Upgrades (Optional Modules)

1. **Drive-Time Isochrones (Valhalla Engine):** If physical barriers (e.g., rivers, mountains, highway dividers) make straight-line hexagonal rings inaccurate for your use case, deploy an open-source **Valhalla** routing container on the backend. Pass the center of the `h3_res7` cell to Valhalla to compute a 10-minute drive-time polygon, convert that polygon into H3 cells via `h3.polygon_to_cells()`, and return those specific records.
2. **Anti-Spoofing / Zero-Permission Fallback (GSMA CAMARA APIs):** For high-security flows or web users who block browser location prompts, integrate telecom carrier Network Location APIs to resolve the device's cell-tower sector directly via their SIM/phone number at the network edge.