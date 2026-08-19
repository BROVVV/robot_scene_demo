#include "go2w_motion_control/leased_sport_client.hpp"

#include <algorithm>
#include <thread>

#include "nlohmann/json.hpp"

namespace go2w_motion_control {
namespace {
constexpr int64_t kMoveApi = 1008;
constexpr int64_t kStopApi = 1003;
}  // namespace

LeasedSportClient::LeasedSportClient(
    rclcpp::Node *node, const std::string &request_topic,
    const std::string &response_topic, const std::string &lease_id_topic,
    const std::string &lease_alive_topic, double lease_status_timeout_sec,
    bool dry_run)
    : node_(node),
      lease_status_timeout_sec_(lease_status_timeout_sec),
      dry_run_(dry_run) {
  request_pub_ = node_->create_publisher<unitree_api::msg::Request>(
      request_topic, rclcpp::QoS(rclcpp::KeepLast(20)).reliable());
  response_sub_ = node_->create_subscription<unitree_api::msg::Response>(
      response_topic, rclcpp::QoS(rclcpp::KeepLast(50)).reliable(),
      [this](const unitree_api::msg::Response::SharedPtr msg) {
        OnResponse(msg);
      });
  const auto lease_qos =
      rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local();
  lease_id_sub_ = node_->create_subscription<std_msgs::msg::UInt64>(
      lease_id_topic, lease_qos,
      [this](const std_msgs::msg::UInt64::SharedPtr msg) {
        lease_id_.store(msg->data);
        std::lock_guard<std::mutex> lock(lease_mutex_);
        lease_update_time_ = std::chrono::steady_clock::now();
      });
  lease_alive_sub_ = node_->create_subscription<std_msgs::msg::Bool>(
      lease_alive_topic, rclcpp::QoS(rclcpp::KeepLast(10)).reliable(),
      [this](const std_msgs::msg::Bool::SharedPtr msg) {
        lease_alive_.store(msg->data);
        std::lock_guard<std::mutex> lock(lease_mutex_);
        lease_update_time_ = std::chrono::steady_clock::now();
      });
}

int64_t LeasedSportClient::NextRequestId() {
  const auto nanoseconds = std::chrono::duration_cast<std::chrono::nanoseconds>(
                               std::chrono::system_clock::now().time_since_epoch())
                               .count();
  int64_t previous = last_request_id_.load();
  int64_t candidate = 0;
  do {
    candidate = std::max<int64_t>(nanoseconds, previous + 1);
  } while (!last_request_id_.compare_exchange_weak(previous, candidate));
  return candidate;
}

void LeasedSportClient::OnResponse(
    const unitree_api::msg::Response::SharedPtr msg) {
  {
    std::lock_guard<std::mutex> lock(response_mutex_);
    const auto key =
        std::make_pair(msg->header.identity.id, msg->header.identity.api_id);
    if (pending_requests_.find(key) == pending_requests_.end()) {
      return;
    }
    responses_[key] = *msg;
  }
  response_changed_.notify_all();
}

RequestResult LeasedSportClient::SendRequest(
    int64_t api_id, const std::string &parameter,
    std::chrono::milliseconds timeout) {
  RequestResult result;
  result.request_id = NextRequestId();
  result.api_id = api_id;
  result.lease_id = CurrentLeaseId();

  EventCallback callback;
  {
    std::lock_guard<std::mutex> lock(callback_mutex_);
    callback = event_callback_;
  }
  if (callback) {
    callback("request", result, parameter);
  }

  if (dry_run_.load()) {
    result.published = false;
    result.response_received = true;
    result.status_code = 0;
    result.response_data = "{\"dry_run\":true}";
    if (callback) {
      callback("response", result, parameter);
    }
    return result;
  }

  if (!LeaseAvailable() || result.lease_id == 0) {
    result.status_code = -9998;
    if (callback) {
      callback("response", result, parameter);
    }
    return result;
  }

  unitree_api::msg::Request request{};
  request.header.identity.id = result.request_id;
  request.header.identity.api_id = api_id;
  request.header.lease.id = static_cast<int64_t>(result.lease_id);
  request.header.policy.priority = 0;
  request.header.policy.noreply = false;
  request.parameter = parameter;
  const auto started = std::chrono::steady_clock::now();
  const auto key = std::make_pair(result.request_id, api_id);
  {
    std::lock_guard<std::mutex> lock(response_mutex_);
    pending_requests_.insert(key);
  }
  request_pub_->publish(request);
  result.published = true;

  std::unique_lock<std::mutex> lock(response_mutex_);
  const bool received = response_changed_.wait_for(
      lock, timeout, [&]() { return responses_.find(key) != responses_.end(); });
  result.round_trip_ms =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() -
                                               started)
          .count();
  if (received) {
    const auto response = responses_.at(key);
    responses_.erase(key);
    result.response_received = true;
    result.status_code = response.header.status.code;
    result.response_data = response.data;
  }
  pending_requests_.erase(key);
  lock.unlock();
  if (callback) {
    callback("response", result, parameter);
  }
  return result;
}

RequestResult LeasedSportClient::SendMove(
    double vx, double vy, double yaw_rate, std::chrono::milliseconds timeout) {
  nlohmann::json parameter = {
      {"x", vx}, {"y", vy}, {"z", yaw_rate}};
  return SendRequest(kMoveApi, parameter.dump(), timeout);
}

RequestResult LeasedSportClient::SendStopMove(
    std::chrono::milliseconds timeout) {
  return SendRequest(kStopApi, "{}", timeout);
}

bool LeasedSportClient::StopRepeatedly(
    int count, std::chrono::milliseconds interval,
    std::chrono::milliseconds response_timeout, int32_t *last_status) {
  bool all_ok = true;
  int32_t status = -9999;
  for (int attempt = 0; attempt < count; ++attempt) {
    const auto result = SendStopMove(response_timeout);
    status = result.status_code;
    all_ok = all_ok && result.response_received && result.status_code == 0;
    if (attempt + 1 < count) {
      std::this_thread::sleep_for(interval);
    }
  }
  if (last_status != nullptr) {
    *last_status = status;
  }
  return all_ok;
}

bool LeasedSportClient::LeaseAvailable() const {
  if (!lease_alive_.load() || lease_id_.load() == 0) {
    return false;
  }
  std::lock_guard<std::mutex> lock(lease_mutex_);
  if (lease_update_time_.time_since_epoch().count() == 0) {
    return false;
  }
  return std::chrono::duration<double>(std::chrono::steady_clock::now() -
                                       lease_update_time_)
             .count() <= lease_status_timeout_sec_;
}

uint64_t LeasedSportClient::CurrentLeaseId() const {
  return lease_id_.load();
}

void LeasedSportClient::SetEventCallback(EventCallback callback) {
  std::lock_guard<std::mutex> lock(callback_mutex_);
  event_callback_ = std::move(callback);
}

void LeasedSportClient::SetDryRun(bool dry_run) { dry_run_.store(dry_run); }

}  // namespace go2w_motion_control
