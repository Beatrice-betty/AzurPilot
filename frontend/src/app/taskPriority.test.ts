import { describe, expect, it } from 'vitest'
import type { Schema } from '../api/types'
import { formatTaskPriority, mergeTaskPriority, moveTaskPriority, parseTaskPriority, reorderTaskPriority, schedulerTasks } from './taskPriority'

/* 以下期望值由 Python 的 module.config.task_priority 在同一份 args.json 上算出，
   用来锁住「界面显示的顺序 = 运行器实际使用的顺序」：两边规则一旦跑偏，测试立刻失败。 */
const PY_DEFAULT_VALUE = 'Restart\n> OpsiCrossMonth\n> Commission > Tactical > Research\n> Exercise\n> Dorm > Meowfficer > Guild > Gacha\n> Reward\n> ShopFrequent > ShopOnce > Shipyard > Freebies\n> PrivateQuarters\n> OpsiExplore\n> OpsiPreventActionPointOverflow\n> Minigame > Awaken\n> OpsiAshBeacon\n> OpsiDaily > OpsiShop > OpsiVoucher > EventShop\n> OpsiAbyssal > OpsiStronghold > OpsiObscure > OpsiArchive\n> Daily > Hard > OpsiAshBeacon > OpsiAshAssist > OpsiMonthBoss\n> Sos > EventSp > EventA > EventB > EventC > EventD\n> RaidDaily > CoalitionSp > WarArchives > MaritimeEscort\n> IslandJuuEatery > IslandJuuCoffee > IslandGrill > IslandTeahouse > IslandRestaurant\n> IslandFarm > IslandRancher > IslandMineForest > IslandDailyGather > IslandManufacture\n> IslandAirDrop > IslandBusiness > IslandDailyOrder > IslandDailyInteract > IslandPearlSell > IslandCargoPreparation\n> Event > Event2 > Event3 > Raid > Hospital > HospitalEvent > Coalition > RaidScuttle > Main > Main2 > Main3\n> OpsiScheduling\n> OpsiMeowfficerFarming\n> GemsFarming\n> Ambush11\n> OpsiHazard1Leveling\n> ThreeOilLowCost\n> OperationHandover'
const PY_AVAILABLE = [
  'Restart', 'Main', 'Main2', 'Main3', 'GemsFarming', 'ThreeOilLowCost', 'Ambush11', 'Event',
  'Event2', 'Event3', 'Raid', 'RaidScuttle', 'Hospital', 'Coalition', 'CoalitionScuttle', 'MaritimeEscort',
  'EventShop', 'WarArchives', 'EventA', 'EventB', 'EventC', 'EventD', 'EventSp', 'RaidDaily',
  'CoalitionSp', 'Commission', 'Tactical', 'Research', 'Dorm', 'Meowfficer', 'Guild', 'Reward',
  'Awaken', 'Secretary', 'OperationHandover', 'Daily', 'Hard', 'Exercise', 'ShopFrequent', 'ShopOnce',
  'Shipyard', 'Gacha', 'Freebies', 'Minigame', 'PrivateQuarters', 'OpsiAshBeacon', 'OpsiAshAssist', 'OpsiExplore',
  'OpsiShop', 'OpsiVoucher', 'OpsiDaily', 'OpsiObscure', 'OpsiAbyssal', 'OpsiArchive', 'OpsiStronghold', 'OpsiMonthBoss',
  'OpsiMeowfficerFarming', 'OpsiHazard1Leveling', 'OpsiScheduling', 'OpsiPreventActionPointOverflow', 'OpsiCrossMonth', 'IslandBusiness', 'IslandFarm', 'IslandRancher',
  'IslandMineForest', 'IslandRestaurant', 'IslandTeahouse', 'IslandGrill', 'IslandJuuEatery', 'IslandJuuCoffee', 'IslandManufacture', 'IslandDailyGather',
  'IslandAirDrop', 'IslandCargoPreparation', 'IslandDailyOrder', 'IslandDailyInteract', 'IslandPearlSell',
]
const PY_DEFAULT_MERGED = [
  'Restart', 'OpsiCrossMonth', 'Commission', 'Tactical', 'Research', 'Exercise', 'Dorm', 'Meowfficer',
  'Guild', 'Gacha', 'Reward', 'ShopFrequent', 'ShopOnce', 'Shipyard', 'Freebies', 'PrivateQuarters',
  'OpsiExplore', 'OpsiPreventActionPointOverflow', 'Minigame', 'Awaken', 'OpsiAshBeacon', 'OpsiDaily', 'OpsiShop', 'OpsiVoucher',
  'EventShop', 'OpsiAbyssal', 'OpsiStronghold', 'OpsiObscure', 'OpsiArchive', 'Daily', 'Hard', 'OpsiAshAssist',
  'OpsiMonthBoss', 'EventSp', 'EventA', 'EventB', 'EventC', 'EventD', 'RaidDaily', 'CoalitionSp',
  'WarArchives', 'MaritimeEscort', 'IslandJuuEatery', 'IslandJuuCoffee', 'IslandGrill', 'IslandTeahouse', 'IslandRestaurant', 'IslandFarm',
  'IslandRancher', 'IslandMineForest', 'IslandDailyGather', 'IslandManufacture', 'IslandAirDrop', 'IslandBusiness', 'IslandDailyOrder', 'IslandDailyInteract',
  'IslandPearlSell', 'IslandCargoPreparation', 'Event', 'Event2', 'Event3', 'Raid', 'Hospital', 'Coalition',
  'RaidScuttle', 'Main', 'Main2', 'Main3', 'OpsiScheduling', 'OpsiMeowfficerFarming', 'GemsFarming', 'Ambush11',
  'OpsiHazard1Leveling', 'ThreeOilLowCost', 'OperationHandover', 'CoalitionScuttle', 'Secretary',
]
const PY_SUBSET_MERGED = [
  'Research', 'Exercise', 'Dorm', 'Meowfficer', 'Guild', 'Gacha', 'Reward', 'ShopFrequent',
  'ShopOnce', 'Shipyard', 'Freebies', 'PrivateQuarters', 'OpsiExplore', 'OpsiPreventActionPointOverflow', 'Minigame', 'Awaken',
  'OpsiAshBeacon', 'OpsiDaily', 'OpsiShop', 'OpsiVoucher', 'EventShop', 'OpsiAbyssal', 'OpsiStronghold', 'OpsiObscure',
  'OpsiArchive', 'Daily', 'Hard', 'OpsiAshAssist', 'OpsiMonthBoss', 'EventSp', 'EventA', 'EventB',
  'EventC', 'EventD', 'RaidDaily', 'CoalitionSp', 'WarArchives', 'MaritimeEscort', 'IslandJuuEatery', 'IslandJuuCoffee',
  'IslandGrill', 'IslandTeahouse', 'IslandRestaurant', 'IslandFarm', 'IslandRancher', 'IslandMineForest', 'IslandDailyGather', 'IslandManufacture',
  'IslandAirDrop', 'IslandBusiness', 'IslandDailyOrder', 'IslandDailyInteract', 'IslandPearlSell', 'IslandCargoPreparation', 'Event', 'Event2',
  'Event3', 'Raid', 'Hospital', 'Coalition', 'RaidScuttle', 'Main', 'Main2', 'Main3',
  'OpsiScheduling', 'OpsiMeowfficerFarming', 'GemsFarming', 'Ambush11', 'OpsiHazard1Leveling', 'ThreeOilLowCost', 'OperationHandover', 'Restart',
  'OpsiCrossMonth', 'Commission', 'Tactical', 'CoalitionScuttle', 'Secretary',
]
const PY_SCRAMBLED_MERGED = [
  'GemsFarming', 'Ambush11', 'OpsiHazard1Leveling', 'ThreeOilLowCost', 'OperationHandover', 'Restart', 'OpsiCrossMonth', 'Commission',
  'Tactical', 'Research', 'Exercise', 'Dorm', 'Meowfficer', 'Guild', 'Gacha', 'Reward',
  'ShopFrequent', 'ShopOnce', 'Shipyard', 'Freebies', 'PrivateQuarters', 'OpsiExplore', 'OpsiPreventActionPointOverflow', 'Minigame',
  'Awaken', 'OpsiAshBeacon', 'OpsiDaily', 'OpsiShop', 'OpsiVoucher', 'EventShop', 'OpsiAbyssal', 'OpsiStronghold',
  'OpsiObscure', 'OpsiArchive', 'Daily', 'Hard', 'OpsiAshAssist', 'OpsiMonthBoss', 'EventSp', 'EventA',
  'EventB', 'EventC', 'EventD', 'RaidDaily', 'CoalitionSp', 'WarArchives', 'MaritimeEscort', 'IslandJuuEatery',
  'IslandJuuCoffee', 'IslandGrill', 'IslandTeahouse', 'IslandRestaurant', 'IslandFarm', 'IslandRancher', 'IslandMineForest', 'IslandDailyGather',
  'IslandManufacture', 'IslandAirDrop', 'IslandBusiness', 'IslandDailyOrder', 'IslandDailyInteract', 'IslandPearlSell', 'IslandCargoPreparation', 'Event',
  'Event2', 'Event3', 'Raid', 'Hospital', 'Coalition', 'RaidScuttle', 'Main', 'Main2',
  'Main3', 'OpsiScheduling', 'OpsiMeowfficerFarming', 'CoalitionScuttle', 'Secretary',
]

describe('优先级文本解析', () => {
  it('换行与 > 都算分隔符', () => {
    expect(parseTaskPriority('A > B\n> C')).toEqual(['A', 'B', 'C'])
  })

  it('兼容全角箭头并去掉注释', () => {
    expect(parseTaskPriority('A ＞ B # 说明\n> C')).toEqual(['A', 'B', 'C'])
  })

  it('重复任务只保留首次出现的位置', () => {
    expect(parseTaskPriority('B\n> A\n> B')).toEqual(['B', 'A'])
  })

  it('空值解析为空列表', () => {
    expect(parseTaskPriority('')).toEqual([])
    expect(parseTaskPriority(undefined)).toEqual([])
    expect(parseTaskPriority('   \n # 只有注释')).toEqual([])
  })

  it('格式化后能被重新解析回同一顺序', () => {
    const tasks = ['Main', 'Commission', 'Research']
    expect(formatTaskPriority(tasks)).toBe('Main\n> Commission\n> Research')
    expect(parseTaskPriority(formatTaskPriority(tasks))).toEqual(tasks)
  })
})

describe('优先级合并', () => {
  it('内置默认顺序与 Python merge_task_priority 一致', () => {
    expect(mergeTaskPriority(PY_DEFAULT_VALUE, PY_DEFAULT_VALUE, PY_AVAILABLE)).toEqual(PY_DEFAULT_MERGED)
  })

  it('用户只写两个任务时，其余任务按默认位置补回', () => {
    expect(mergeTaskPriority('Research > Commission', PY_DEFAULT_VALUE, PY_AVAILABLE)).toEqual(PY_SUBSET_MERGED)
  })

  it('带注释与换行的用户顺序同样与 Python 一致', () => {
    expect(mergeTaskPriority('GemsFarming # 注释\n> Main', PY_DEFAULT_VALUE, PY_AVAILABLE)).toEqual(PY_SCRAMBLED_MERGED)
  })

  it('补入的新任务插在默认顺序的前一个已知任务之后', () => {
    expect(mergeTaskPriority('C > A', 'A > B > C > D', ['A', 'B', 'C', 'D'])).toEqual(['C', 'D', 'A', 'B'])
    expect(mergeTaskPriority('B', 'A > B > C', ['A', 'B', 'C'])).toEqual(['A', 'B', 'C'])
  })

  it('已不存在的任务被丢弃，默认列表里没有的任务排到最后', () => {
    expect(mergeTaskPriority('X > B', 'A > B', ['A', 'B', 'Z'])).toEqual(['A', 'B', 'Z'])
    expect(mergeTaskPriority('Z > A', 'A > B', ['A', 'B', 'Z'])).toEqual(['Z', 'A', 'B'])
  })

  it('没有可用任务清单时回落到默认顺序', () => {
    expect(mergeTaskPriority('B > A', 'A > B', [])).toEqual(['B', 'A'])
  })
})

describe('拖动排序', () => {
  it('把任务移动到目标位置，其余顺序不变', () => {
    expect(moveTaskPriority(['A', 'B', 'C', 'D'], 3, 1)).toEqual(['A', 'D', 'B', 'C'])
    expect(moveTaskPriority(['A', 'B', 'C', 'D'], 0, 2)).toEqual(['B', 'C', 'A', 'D'])
  })

  it('原地或越界移动不改动顺序', () => {
    expect(moveTaskPriority(['A', 'B'], 1, 1)).toEqual(['A', 'B'])
    expect(moveTaskPriority(['A', 'B'], -1, 1)).toEqual(['A', 'B'])
    expect(moveTaskPriority(['A', 'B'], 0, 5)).toEqual(['A', 'B'])
  })

  it('拖动落点按「插到第 N 项之前」换算', () => {
    expect(reorderTaskPriority(['A', 'B', 'C', 'D'], 3, 0)).toEqual(['D', 'A', 'B', 'C'])
    expect(reorderTaskPriority(['A', 'B', 'C', 'D'], 0, 4)).toEqual(['B', 'C', 'D', 'A'])
    expect(reorderTaskPriority(['A', 'B', 'C', 'D'], 1, 3)).toEqual(['A', 'C', 'B', 'D'])
  })

  it('落在自己身上或紧邻位置时顺序不变', () => {
    expect(reorderTaskPriority(['A', 'B', 'C'], 1, 1)).toEqual(['A', 'B', 'C'])
    expect(reorderTaskPriority(['A', 'B', 'C'], 1, 2)).toEqual(['A', 'B', 'C'])
    expect(reorderTaskPriority(['A', 'B', 'C'], 2, 3)).toEqual(['A', 'B', 'C'])
  })
})

describe('调度任务清单', () => {
  const args = {
    Alas: {Scheduler: {Command: {type: 'input', value: 'Alas'}}},
    Main: {Scheduler: {Command: {type: 'input', value: 'Main'}}, Campaign: {Name: {type: 'input', value: '12-4'}}},
    Main2: {Scheduler: {Command: {type: 'input', value: 'Main'}}},
    FleetInfo: {FleetInfo: {Result: {type: 'storage', value: {}}}},
  } as unknown as Schema['args']

  it('只取带 Scheduler.Command 的任务并去重', () => {
    expect(schedulerTasks(args)).toEqual(['Alas', 'Main'])
  })

  it('缺少参数模板时返回空清单', () => {
    expect(schedulerTasks(undefined)).toEqual([])
  })
})
