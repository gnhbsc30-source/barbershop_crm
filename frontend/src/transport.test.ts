import {strict as assert} from 'node:assert';
import {test} from 'node:test';
import {decodeJson, encodeJson, resourceId, slotQuery} from './transport.ts';

for (const value of ['9007199254740991', '9007199254740993', '9223372036854775807']) {
  test('preserves identifier ' + value, () => {
    const id = resourceId(value);
    const wire = '{"appointment_id":' + value + ',"service_ids":[' + value + '],"total_duration_minutes":20}';
    assert.deepEqual(decodeJson(wire), {appointment_id: id, service_ids: [id], total_duration_minutes: 20});
    assert.equal(encodeJson({appointment_id: id, service_ids: [id], total_duration_minutes: 20}), wire);
    assert.equal(new URL('/appointments/' + id, 'http://example.test').pathname.split('/').at(-1), id);
    const query = new URLSearchParams(slotQuery('2027-02-28', id, [id]));
    assert.equal(query.get('staff_id'), id);
    assert.equal(query.get('service_id'), id);
    assert.equal(query.get('date'), '2027-02-28');
  });
}

test('rejects invalid and out-of-range identifiers', () => {
  for (const value of ['0', '01', '1.0', '1e2', '9223372036854775808']) {
    assert.throws(() => resourceId(value));
  }
  assert.throws(() => decodeJson('{"staff_id":"10"}'));
  assert.throws(() => encodeJson({service_ids: [9007199254740993]}));
});

test('keeps business-local times and exact money unchanged', () => {
  const payload = '{"start_datetime":"2027-03-28T09:00:00","total_price":"80.00"}';
  assert.equal(encodeJson(decodeJson(payload)), payload);
});
