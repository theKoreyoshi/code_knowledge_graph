#include "pipeline.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>

namespace demo {

void MemorySink::push(const Sample &sample)
{
    samples_.push_back(sample);
}

Pipeline::Pipeline(std::shared_ptr<Sink> sink) : sink_(std::move(sink))
{
}

Pipeline::~Pipeline() = default;

bool Pipeline::accept(const Sample &sample) const
{
    return sample.valid && !sample.name.empty() && std::isfinite(sample.value);
}

Stage Pipeline::run(const std::vector<Sample> &input)
{
    Accumulator<double> accumulator;

    stage_ = Stage::Running;
    for (const Sample &sample : input) {
        if (!accept(sample)) {
            stage_ = Stage::Failed;
            continue;
        }
        accumulator.add(DEMO_SCALE(sample.value, 2.0));
        if (observer_) {
            observer_(sample);
        }
        if (sink_) {
            sink_->push(sample);
        }
        processed_++;
    }
    if (stage_ == Stage::Running) {
        stage_ = Stage::Idle;
    }
    std::cout << DEMO_LOUD("mean") << ' ' << accumulator.mean() << std::endl;
    return stage_;
}

std::string describe(Stage stage)
{
    switch (stage) {
    case Stage::Idle:
        return "idle";
    case Stage::Running:
        return "running";
    case Stage::Failed:
        return "failed";
    }
    return "unknown";
}

std::vector<Sample> make_samples(std::size_t count)
{
    std::vector<Sample> samples;
    samples.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        samples.push_back(Sample{"sample", static_cast<double>(i), i % 7 != 0});
    }
    return samples;
}

}  // namespace demo
