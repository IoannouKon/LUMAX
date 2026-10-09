# Documentation figures and data

[Project overview](../README.md)

## Paper source

The documentation uses the user-supplied manuscript **LUMIXED: LUT-Based MIXED-Precision GeMM Accelerator for Quantized DNN Inference**, file `LUMIXED_ICCAD2026-1.pdf` (9 PDF pages). Figures are attributed to that manuscript; this documentation does not assert a publication status or DOI. The PDF itself is not copied into the repository.

Source SHA-256:

```text
ad5a5494b76ad3274b46c537084ec8ebd7ebbce383cc97a8f36b377eb626caab
```

All six PNGs are direct raster crops of the original figures. Their plotted values, labels, and visual contents are unchanged. Captions are supplied beside each image in the READMEs. Page numbers below count PDF pages starting at 1.

| Figure | PDF page | Asset | Used for |
|---|---:|---|---|
| 1 | 3 | [Dataflow](assets/paper/figure-1-dataflow.png) | Architectural overview |
| 2 | 4 | [Product selection](assets/paper/figure-2-selection.png) | Addressing, correction, and reuse |
| 3 | 5 | [Pipeline timing](assets/paper/figure-3-pipeline.png) | Pipelining and double buffering |
| 4 | 7 | [Resource and area plots](assets/paper/figure-4-resources.png) | FPGA resources and ASIC area |
| 5 | 8 | [ViT Pareto plots](assets/paper/figure-5-vit-pareto.png) | Accuracy/cycle exploration |
| 6 | 8 | [QKV bitwidths](assets/paper/figure-6-qkv.png) | Selected per-layer precision assignments |

Regenerate the crops with Python 3 and Poppler's `pdftoppm`, from the repository root:

```bash
python3 docs/extract_paper_figures.py /path/to/LUMIXED_ICCAD2026-1.pdf
```

[extract_paper_figures.py](extract_paper_figures.py) records the page/crop coordinates. These coordinates apply to this specific manuscript layout. The design guide's Mermaid system diagram is a new explanatory diagram based on the source structure, not a figure extracted from the paper. No AI-generated images or reconstructed experimental data are used.

## Final validation

The [final report](../LUMAX/software/tests/src/Log/run_20261008_193240/total_results.md) and [CSV](../LUMAX/software/tests/src/Log/run_20261008_193240/total_results.csv) contain the retained 180 passing tests and hardware counters.
