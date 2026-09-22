* 金标准用例 5：折叠共源共栅（共源共栅 + 有源负载 + 电流镜）
* 回归目标：
*   * 共源共栅：M4/M6、M5/M7 应被识别（上管源极接在下管漏极、栅接不同固定偏置）
*   * 有源负载：接在输入对折叠节点上的 PMOS 电流源必须出现在模块列表里
*   * 差分对叠在尾电流源上（M1/M3）不得被误判为共源共栅
.subckt FCASC vinp vinn vout vdd vss vb1 vb2 vb3
M1  fo1  vinp tail vss nmos w=10u l=1u m=1
M2  fo2  vinn tail vss nmos w=10u l=1u m=1
M3  tail vb1  vss  vss nmos w=5u  l=1u m=1
M4  n1   vb2  fo1  fo1 pmos w=20u l=1u m=1
M5  n2   vb2  fo2  fo2 pmos w=20u l=1u m=1
M6  fo1  vb3  vdd  vdd pmos w=20u l=1u m=1
M7  fo2  vb3  vdd  vdd pmos w=20u l=1u m=1
M8  nb   nb   vdd  vdd pmos w=10u l=1u m=1
M9  vout nb   vdd  vdd pmos w=10u l=1u m=1
M10 n2   nb   vdd  vdd pmos w=10u l=1u m=1
.ends FCASC
