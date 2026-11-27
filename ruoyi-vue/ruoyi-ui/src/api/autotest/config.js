import request from '@/utils/request'

// 查询测试配置列表
export function listConfig(query) {
  return request({
    url: '/autotest/config/list',
    method: 'get',
    params: query
  })
}

// 获取默认测试配置
export function getDefaultConfig() {
  return request({
    url: '/autotest/config/default',
    method: 'get'
  })
}

// 查询测试配置详细
export function getConfig(configId) {
  return request({
    url: '/autotest/config/' + configId,
    method: 'get'
  })
}

// 新增测试配置
export function addConfig(data) {
  return request({
    url: '/autotest/config',
    method: 'post',
    data: data
  })
}

// 修改测试配置
export function updateConfig(data) {
  return request({
    url: '/autotest/config',
    method: 'put',
    data: data
  })
}

// 删除测试配置
export function delConfig(configId) {
  return request({
    url: '/autotest/config/' + configId,
    method: 'delete'
  })
}
