* 金标准用例 7：MOS 电容 / Dummy 区分 + CMFB 网络
* 回归目标：
*   * MC1（D/S/B 全接 vss）应识别为 MOS 电容，而不是被当成 Dummy 剔除
*   * MDUM（命名含 dum）应识别为 Dummy
*   * 与 vcmfb 网络相连的器件应触发共模反馈识别
.subckt WCM vinp vinn vout vdd vss vcmfb
MC1  vss vss vss vss nmos w=10u l=10u m=1
MDUM vss dum_g vss vss nmos w=1u l=1u m=1
M1   n1  vinp t   vss nmos w=4u l=1u m=1
M2   n2  vinn t   vss nmos w=4u l=1u m=1
M3   t   nb   vss vss nmos w=8u l=1u m=1
M4   vcmfb vout vss vss nmos w=4u l=1u m=1
.ends WCM
