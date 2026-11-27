import request from '@/utils/request'

// 查询测试批次列表
export function listBatch(query) {
  return request({
    url: '/autotest/batch/list',
    method: 'get',
    params: query
  })
}

// 查询测试批次详细
export function getBatch(batchId) {
  return request({
    url: '/autotest/batch/' + batchId,
    method: 'get'
  })
}

// 删除测试批次
export function delBatch(batchId) {
  return request({
    url: '/autotest/batch/' + batchId,
    method: 'delete'
  })
}
