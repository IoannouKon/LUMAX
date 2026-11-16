/* Generated offline with controller-events-v7 for this experiment only.
 * FP16; physical lanes=8, RF=4, maximum YS=4, DMA IDs=8.
 * Call lumax_select_bank_grouping(lumax_grouping_for_shape(...)) before preload.
 * This table contains predictions; comparison.csv records measured regret. */
#ifndef LUMAX_GROUPING_SCHEDULE_H
#define LUMAX_GROUPING_SCHEDULE_H
static inline unsigned lumax_grouping_for_shape(unsigned r, unsigned k, unsigned n, unsigned wb) {
    if (r==1 && k==32 && n==16 && wb==8) return 1;
    if (r==1 && k==32 && n==16 && wb==4) return 1;
    if (r==1 && k==32 && n==16 && wb==2) return 1;
    if (r==4 && k==32 && n==16 && wb==8) return 4;
    if (r==4 && k==32 && n==16 && wb==4) return 4;
    if (r==4 && k==32 && n==16 && wb==2) return 4;
    if (r==5 && k==33 && n==9 && wb==8) return 1;
    if (r==5 && k==33 && n==9 && wb==4) return 2;
    if (r==5 && k==33 && n==9 && wb==2) return 2;
    if (r==8 && k==64 && n==32 && wb==8) return 4;
    if (r==8 && k==64 && n==32 && wb==4) return 4;
    if (r==8 && k==64 && n==32 && wb==2) return 4;
    if (r==3 && k==129 && n==17 && wb==8) return 1;
    if (r==3 && k==129 && n==17 && wb==4) return 1;
    if (r==3 && k==129 && n==17 && wb==2) return 1;
    return 4; /* Unknown shape: fixed baseline grouping. */
}
#endif
