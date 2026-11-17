# Source revision note

The off and aligned simulator builds use their own immutable `source/` and `cache/.../scala/` trees, with hashes recorded by the runner. Before the deferred build, three small corrections were copied into the parent source snapshot: the controller-layout comment, an elaboration requirement for the supported 8-byte DMA beat size, and carry-preserving addition when computing ceil(K/G). All runtime shapes in the first two modes fit their original addition width. The deferred build uses these corrections. No running simulator or unrelated job was changed.

The live source includes the corrections. An additional aligned K=511 boundary run is planned to check carry preservation.
