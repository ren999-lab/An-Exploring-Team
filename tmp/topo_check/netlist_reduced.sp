* 全差分运放示例网表（两级 Miller 补偿 + 理想 CMFB，供本地测试）
.include process_models.sp

* ======================================================================
* 本文件由 agent2_topology 自动生成：第②问 参数约减结果（变量化网表）
* 变量总数 27 = 自由变量 15 + 联动变量 12
* PDK 取值约束：M: W 0.13u~20u, L 0.13u~10u, m 1~20(整数)；R: Seg 1~20(整数)；C: L 0.13u~20u (W 沿用同区间)
* 变量命名与第③问参数文件一致：<器件名>_<参数>（如 M7_w / M7_m / R1_seg）
* 所有变量都在下方 .param 块中声明；联动变量以表达式引用参考变量，
* 因此本网表可直接自洽仿真（变量名与 variables.csv 一一对应）。
* 联动关系（非独立变量，随参考变量按初始比例缩放）：
*   M3_w = M7_w    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M3_l = M7_l    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M3_m = M7_m    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M4_w = M7_w    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M4_l = M7_l    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M4_m = M7_m    (电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）)
*   M2_w = M1_w    (匹配联动，强制与首器件同尺寸（非独立变量）)
*   M2_l = M1_l    (匹配联动，强制与首器件同尺寸（非独立变量）)
*   M2_m = M1_m    (匹配联动，强制与首器件同尺寸（非独立变量）)
*   M6_w = M5_w    (匹配联动，强制与首器件同尺寸（非独立变量）)
*   M6_l = M5_l    (匹配联动，强制与首器件同尺寸（非独立变量）)
*   M6_m = M5_m    (匹配联动，强制与首器件同尺寸（非独立变量）)
* 自由变量（可交给第③问优化器）：
*   M7_w = 4u  取值 0.13~20.0um    (电流镜参考臂，自由变量)
*   M7_l = 0.5u  取值 0.13~10.0um    (电流镜参考臂，自由变量)
*   M7_m = 1  取值 1~20    (电流镜参考臂，自由变量)
*   M1_w = 2u  取值 0.13~20.0um    (输入对管（差分对）各管强制同尺寸，合并为单变量)
*   M1_l = 0.5u  取值 0.13~10.0um    (输入对管（差分对）各管强制同尺寸，合并为单变量)
*   M1_m = 1  取值 1~20    (输入对管（差分对）各管强制同尺寸，合并为单变量)
*   M0_w = 4u  取值 0.13~20.0um    (尾电流源成员，保留为独立变量)
*   M0_l = 0.5u  取值 0.13~10.0um    (尾电流源成员，保留为独立变量)
*   M0_m = 2  取值 1~20    (尾电流源成员，保留为独立变量)
*   M5_w = 8u  取值 0.13~20.0um    (匹配器件各管强制同尺寸，合并为单变量)
*   M5_l = 0.5u  取值 0.13~10.0um    (匹配器件各管强制同尺寸，合并为单变量)
*   M5_m = 2  取值 1~20    (匹配器件各管强制同尺寸，合并为单变量)
*   RC1_value = 1k    (无源器件（RC/去耦，用途待确认）成员，保留为独立变量)
*   CC1_value = 1p    (无源器件（RC/去耦，用途待确认）成员，保留为独立变量)
*   CDEC_value = 10p    (无源器件（RC/去耦，用途待确认）成员，保留为独立变量)
* ======================================================================
.param M7_w=4u
.param M7_l=0.5u
.param M7_m=1
.param M1_w=2u
.param M1_l=0.5u
.param M1_m=1
.param M0_w=4u
.param M0_l=0.5u
.param M0_m=2
.param M5_w=8u
.param M5_l=0.5u
.param M5_m=2
.param RC1_value=1k
.param CC1_value=1p
.param CDEC_value=10p
.param M3_w=M7_w
.param M3_l=M7_l
.param M3_m=M7_m
.param M4_w=M7_w
.param M4_l=M7_l
.param M4_m=M7_m
.param M2_w=M1_w
.param M2_l=M1_l
.param M2_m=M1_m
.param M6_w=M5_w
.param M6_l=M5_l
.param M6_m=M5_m
.subckt OPA inp inn outp outn vdd vss
*--- 尾电流源 ---
M0 tail biasp vdd vdd PM w={M0_w} l={M0_l} m={M0_m}
*--- 输入对管 ---
M1 drn1 inp tail vss NM w={M1_w} l={M1_l} m={M1_m}
M2 drn2 inn tail vss NM w={M2_w} l={M2_l} m={M2_m}
*--- PMOS 电流镜负载 ---
M3 drn1 mirp vdd vdd PM w={M3_w} l={M3_l} m={M3_m}
M4 drn2 mirp vdd vdd PM w={M4_w} l={M4_l} m={M4_m}
*--- 输出级共源放大 ---
M5 outn biasn drn1 vss NM w={M5_w} l={M5_l} m={M5_m}
M6 outp biasn drn2 vss NM w={M6_w} l={M6_l} m={M6_m}
*--- 偏置电流镜（参考臂二极管连接）---
M7 mirp mirp vdd vdd PM w={M7_w} l={M7_l} m={M7_m}
*--- Dummy 器件 ---
MDUM1 vss dummy_g vss vss NM w=1u l=0.5u m=1
MDUM2 vdd dummy_p vdd vdd PM w=1u l=0.5u m=1
*--- 米勒补偿 ---
RC1 drn1 outn comp1 {RC1_value}
CC1 outn comp1 vss {CC1_value}
*--- 电源去耦电容 ---
CDEC vdd vss {CDEC_value}
.ends OPA
