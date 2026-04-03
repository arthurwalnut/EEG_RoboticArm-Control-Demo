# 这是什么？
这是一个简单demo，在windows端连接来自EmotivBCI公司的Emotiv Flex2脑电（EEG）信号帽，在linux端连接来自AgileX公司的Piper机械臂和来自Elite Robots的EC系列协作机械臂。通过实时收集脑电信号对两个不同的机械臂发出对应指令使其运动。

# 怎么使用？
在linux端，你需要首先连接两种机械臂，并将receiver.py，eat_elite.py和drink_piper.py放置在同一个路径下。运行receiver.py开始监听指定UDP端口。

在windows端，首先使用官方软件连接到脑电信号帽并测试EEG信号质量。接下来使用EmotivBCI官方提供的账号以及调用接口使用的token（参见：[EMOTIV](https://emotiv.gitbook.io/cortex-api)），并检查linux设备所在ip地址，将它们写入sender.py并运行。

在运行sender.py后，根据界面指引操作即可完成训练及测试过程。如果需要减少/增加运行过程中的不同类别训练次数，请在sender.py中进行修改。

# [效果演示](https://github.com/arthurwalnut/EEG_RoboticArm-Control-Demo/releases/download/v1.0.0/demo.mp4)

注：视频仅展示了测试阶段的效果
