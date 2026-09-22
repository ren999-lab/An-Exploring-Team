* 全差分运放示例网表（两级 Miller 补偿 + 理想 CMFB，供本地测试）
.include process_models.sp

.subckt OPA inp inn outp outn vdd vss
*--- 尾电流源 ---
M0  tail  biasp vdd vdd PM w=4u l=0.5u m=2
*--- 输入对管 ---
M1  drn1 inp  tail vss NM w=2u l=0.5u m=1
M2  drn2 inn  tail vss NM w=2u l=0.5u m=1
*--- PMOS 电流镜负载 ---
M3  drn1 mirp vdd vdd PM w=4u l=0.5u m=1
M4  drn2 mirp vdd vdd PM w=4u l=0.5u m=1
*--- 输出级共源放大 ---
M5  outn biasn drn1 vss NM w=8u l=0.5u m=2
M6  outp biasn drn2 vss NM w=8u l=0.5u m=2
*--- 偏置电流镜（参考臂二极管连接）---
M7  mirp mirp vdd vdd PM w=4u l=0.5u m=1
*--- Dummy 器件 ---
MDUM1 vss dummy_g vss vss NM w=1u l=0.5u m=1
MDUM2 vdd dummy_p vdd vdd PM w=1u l=0.5u m=1
*--- 米勒补偿 ---
RC1 drn1 outn comp1 1k
CC1 outn comp1 vss 1p
*--- 电源去耦电容 ---
CDEC vdd vss 10p
.ends OPA
