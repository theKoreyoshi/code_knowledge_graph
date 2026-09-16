#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace demo {

#define DEMO_UNUSED(x) ((void)(x))
#define DEMO_SCALE(v, k) ((v) * (k))
#define DEMO_LOUD(s) ("[" + std::string(s) + "]")

enum class Stage { Idle, Running, Failed };

struct Sample {
    std::string name;
    double value = 0.0;
    bool valid = true;
};

class Sink {
public:
    virtual ~Sink() = default;
    virtual void push(const Sample &sample) = 0;
    virtual std::size_t size() const = 0;
};

class MemorySink : public Sink {
public:
    void push(const Sample &sample) override;
    std::size_t size() const override { return samples_.size(); }
    const std::vector<Sample> &samples() const { return samples_; }

private:
    std::vector<Sample> samples_;
};

template <typename T>
class Accumulator {
public:
    void add(const T &value) { total_ += value; ++count_; }
    T total() const { return total_; }
    std::size_t count() const { return count_; }
    T mean() const { return count_ ? static_cast<T>(total_ / static_cast<double>(count_)) : T{}; }

private:
    T total_{};
    std::size_t count_ = 0;
};

class Pipeline {
public:
    explicit Pipeline(std::shared_ptr<Sink> sink);
    ~Pipeline();

    Stage run(const std::vector<Sample> &input);
    Stage state() const { return stage_; }
    std::size_t processed() const { return processed_; }
    void on_sample(std::function<void(const Sample &)> callback) { observer_ = std::move(callback); }

private:
    bool accept(const Sample &sample) const;

    std::shared_ptr<Sink> sink_;
    std::function<void(const Sample &)> observer_;
    Stage stage_ = Stage::Idle;
    std::size_t processed_ = 0;
};

std::string describe(Stage stage);
std::vector<Sample> make_samples(std::size_t count);

}  // namespace demo
