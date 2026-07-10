SteelOptima Client Workspace: Refined Functional & Architectural SpecificationSystem Focus: End-to-End Image Manipulation, Confidence-Tiered Overlays, and Aggregated BOM ManagementDocument Version: 3.0.0 (Production-Ready Target)1. EXTENDED INGESTION PIPELINE & PRE-PROCESSING WORKFLOWBefore sending any source document to the backend processing nodes, the client interface must prevent junk data ingestion. This stage introduces a verification and cropping interface to isolate structural features.1.1 Multi-Format File IngestionSupported Core Mime-Types: application/pdf, image/jpeg, image/jpg, image/png.Dropzone Component Updates: The upload dropzone interface converts uploaded multi-page files or raw pixel arrays into a client-side layout canvas viewport.1.2 Interactive Document Preview & Bounding Crop ToolUpon dropping a file, instead of instantly invoking the backend extraction pipeline, the interface pauses on an Ingestion Preview Screen:+--------------------------------------------------------------------------+
|  [Preview Canvas: A (3).pdf]                                 [Cancel]    |
+--------------------------------------------------------------------------+
|  +--------------------------------------------------------------------+  |
|  | [....................... Crop Box Selection .....................] |  |
|  | :  +----------------------------------------------------------+  : |  |
|  | :  |                                                          |  : |  |
|  | :  |              STRUCTURAL STEEL PROFILE LAYOUT             |  : |  |
|  | :  |                                                          |  : |  |
|  | :  +----------------------------------------------------------+  : |  |
|  | [................................................................] |  |
|  +--------------------------------------------------------------------+  |
|                                                                          |
|  * Drag the corner anchors to crop out title blocks, margins, or legends. |
|                                                                          |
|                                              [Confirm & Send to Extract] |
+--------------------------------------------------------------------------+
Visual Overlay: A semitransparent mask overlays the document preview, featuring an adjustable rectangular bounding region with 8-point corner and edge drag handles.Crop Processing Logic: The UI calculates the relative bounding offsets (x_min, y_min, x_max, y_max) as percentages or normalized pixel integers relative to the original image dimensions.Payload Transmission: Clicking "Confirm & Send to Extract" dispatches the file binary alongside the normalized cropping coordinates to POST /api/v1/blueprint/process. This ensures the backend VLM and feature harvesters ignore irrelevant frames, margins, titles, and text legends.2. LANDING PAGE HISTORICAL LOG RUN UPDATESThe main portal (SteelOptima dashboard) tracks historical file workflows with explicit processing state indicators.+--------------------------------------------------------------------------+
|  SteelOptima -- Historical Run Log                                       |
+--------------------------------------------------------------------------+
|  ■ A (3).pdf ........... 1 page · 3.7.2026, 14:41:05 ........ [APPROVED] |
|  ■ A (4).pdf ........... 1 page · 3.7.2026, 14:38:37 ........ [PENDING]  |
|  ■ Doc_HK3573.pdf ...... 1 page · 2.9.2026, 14:31:08 ........ [APPROVED] |
+--------------------------------------------------------------------------+
State IndicatorsPending Status Badge: Applied to any document requiring human confirmation inside the Interactive Workspace.Approved Status Badge: When an item is finalized via the workspace, its row inside the primary file log updates with an irreversible approved status badge. The row item locks to prevent accidental processing runs.3. INTERACTIVE VALIDATION WORKSPACE (VIEW 2 REFINEMENTS)3.1 Initial Resolution & Canvas Scaling RulesNaked-Eye Aspect-Ratio Fit: When the canvas workspace instantiates, the workspace dynamic dimensions compute a viewport bounding matrix that displays the entire blueprint drawing natively within the screen space.Zoom Avoidance Rule: No immediate manual zoom-in or zoom-out actions are forced upon the operator during load states. The structural layout is automatically centered and scaled to fit the user's monitor space.3.2 Confidence-Tiered Vector OverlaysThe flat sidebar tracking array is replaced by an automated confidence filtering layer:High-Confidence Cavities ($\ge$ 90% Confidence Score):Canvas Rendering Style: Solid Yellow bounding overlay path outlines.Data Handling: These features are instantly aggregated into the right-hand Bill of Materials (BOM) summary grid on load.Low-Confidence Cavities ($\le$ 89% Confidence Score):Canvas Rendering Style: Thick, pulsing Red bounding overlay path outlines to immediately flag human inspection tasks.Data Handling: Excluded from production tallies until validated or modified by the operator.+-------------------------------------------------------------------+
|  [Toolbox]  ✎ Freeform Draw  |  🔍 Zoom  |  ↺ Reset View            |
+-------------------------------------------------------------------+
|                                                                   |
|     (High Confidence: >=90%)        (Low Confidence: <=89%)       |
|         +--------------+                +--------------+          |
|         |    Yellow    |                |  Pulsing Red |          |
|         |    Stroke    |                |    Stroke    |          |
|         +--------------+                +--------------+          |
|                                                                   |
+-------------------------------------------------------------------+
3.3 Amorphous & Freeform Poly-Path Creation EngineWhen the operator toggles the manual addition tool (+ Add), the drawing layer supports both standard bounding shapes and freeform shapes for irregular cavities uncaptured by basic heuristics:Freeform Path Interaction Mode:Clicking "Freeform Draw" unlocks a multi-point vector path tool on the canvas.The operator clicks sequentially to append points ($P_1, P_2, \dots, P_n$) outlining the amorphous feature boundaries.Double-clicking or clicking back on $P_1$ seals the vector polygon shell.The application prompts a modal asking for structural classification metadata (e.g., Slot, Hole, Custom profile type) and aggregates this new geometric record into the active dataset.4. ANALYTICAL CONSOLIDATED BOM SHEET GRIDThe client workspace eliminates flat scrolling lists of hundreds of sequential items in favor of a clean Aggregated BOM Summary Table.+----------------------------------------------------------------------------+
| Bill of Materials (BOM) Summary Aggregation Sheet                          |
+----------------------------------------------------------------------------+
| Shape Profile | Calculated Dimensions | Active Quantity | Status Check     |
+---------------+-----------------------+-----------------+------------------+
| Slot          | 25x20 mm              | 24x             | [✓ Verified]     |
| Circle        | Ø 22.0 mm             | 124x            | [▲ 2 Under Review|
| Irregular     | Custom Poly-Path      | 2x              | [✓ Manually Added|
+----------------------------------------------------------------------------+
| [APPROVE & FINALIZE WORK ORDER]                                            |
+----------------------------------------------------------------------------+
4.1 Grid Architecture & State UpdatesConsolidation Pipeline: Shapes are bucketed using their structural profile properties and dim signatures (groupKey). The table prints singular summarized rows displaying structural totals.Interactive Toggles (Add/Remove):Clicking an omission button next to an overlay removes it from the array, decrementing the grouped count in the table instantly.Reverting a deletion or manually tracing a shape recalculates the matching row target value immediately.4.2 Single-Click Final Sign-Off FlowThe "Approve & Finalize" Trigger: Located at the bottom of the BOM grid.Payload Dispersal: Clicking this action collects all modified geometric arrays and pushes a definitive payload to POST /api/v1/blueprint/{jobId}/finalize. This locks the active layout from changes and sets the file status to "Approved" on the landing page log.