#include "pipeline.hpp"

#include <iostream>
#include <map>

namespace {

std::map<std::string, std::size_t> tally(const std::vector<demo::Sample> &samples)
{
    std::map<std::string, std::size_t> counts;
    for (const auto &sample : samples) {
        counts[sample.name] += 1;
    }
    return counts;
}

}  // namespace

int main()
{
    auto sink = std::make_shared<demo::MemorySink>();
    demo::Pipeline pipeline(sink);
    const auto samples = demo::make_samples(32);

    pipeline.on_sample([](const demo::Sample &sample) {
        if (!sample.valid) {
            std::cerr << "skipped" << std::endl;
        }
    });

    const demo::Stage stage = pipeline.run(samples);
    const auto counts = tally(sink->samples());

    std::cout << demo::describe(stage) << " " << pipeline.processed() << " "
              << counts.size() << std::endl;
    return stage == demo::Stage::Failed ? 1 : 0;
}
